# src/supsrc/runtime/orchestrator.py

"""
High-level coordinator for the supsrc watch process.
Manages lifecycle of all runtime components.
"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, TypeAlias, cast

import structlog
from rich.console import Console
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from supsrc.config import SupsrcConfig, load_config
from supsrc.engines.git import GitEngine, GitRepoSummary
from supsrc.exceptions import ConfigurationError, MonitoringSetupError
from supsrc.monitor import MonitoredEvent, MonitoringService
from supsrc.protocols import RepositoryEngine
from supsrc.state import RepositoryState, RepositoryStatus
from supsrc.telemetry import StructLogger

from .action_handler import ActionHandler
from .event_processor import EventProcessor
from .tui_interface import TUIInterface

if TYPE_CHECKING:
    from supsrc.tui.app import SupsrcTuiApp


log: StructLogger = structlog.get_logger("runtime.orchestrator")
RepositoryStatesMap: TypeAlias = dict[str, RepositoryState]
RULE_EMOJI_MAP: dict[str, str] = {
    "supsrc.rules.inactivity": "⏳",
    "supsrc.rules.save_count": "💾",
    "supsrc.rules.manual": "✋",
    "default": "⚙️",
}


class WatchOrchestrator:
    """Instantiates and coordinates all runtime components for the watch command."""

    def __init__(
        self,
        config_path: Path,
        shutdown_event: asyncio.Event,
        app: Optional["SupsrcTuiApp"] = None,
        console: Console | None = None,
    ):
        self.config_path = config_path
        self.shutdown_event = shutdown_event
        self.app = app
        self.console = console
        self.event_queue: asyncio.Queue[MonitoredEvent] = asyncio.Queue()
        self.repo_states: RepositoryStatesMap = {}
        self.repo_engines: dict[str, RepositoryEngine] = {}
        self.monitor_service: MonitoringService | None = None
        self.event_processor: EventProcessor | None = None
        self.config: SupsrcConfig | None = None
        self._is_paused = False
        self.config_observer: Observer | None = None

    async def run(self) -> None:
        """Main execution method: setup, run, and cleanup."""
        log.info("Orchestrator run sequence starting.")
        processor_task = None
        tui = TUIInterface(self.app)

        try:
            try:
                self.config = await asyncio.to_thread(load_config, self.config_path)
            except ConfigurationError as e:
                log.critical("Failed to load or validate config", error=str(e), exc_info=True)
                tui.post_log_update(None, "CRITICAL", f"Config Error: {e}")
                await asyncio.sleep(0.1)
                return

            enabled_repos = await self._initialize_repositories(self.config, tui)

            action_handler = ActionHandler(self.config, self.repo_states, self.repo_engines, tui)
            self.event_processor = EventProcessor(
                self, self.config, self.event_queue, self.shutdown_event, action_handler, self.repo_states, tui
            )

            self.monitor_service = self._setup_monitoring(self.config, enabled_repos, tui)
            if self.monitor_service:
                try:
                    self.monitor_service.start()
                    if not self.monitor_service.is_running:
                        log.error("Monitoring service for repositories failed to start silently.")
                        tui.post_log_update(None, "ERROR", "Filesystem monitoring service failed to start.")
                except Exception as e:
                    log.critical("Failed to start filesystem monitoring service", error=str(e), exc_info=True)
                    tui.post_log_update(None, "CRITICAL", f"FATAL: Filesystem monitor failed: {e}")

            self.setup_config_watcher(tui)
            if self.config_observer:
                try:
                    self.config_observer.start()
                except Exception as e:
                    log.critical("Failed to start configuration file watcher", error=str(e), exc_info=True)
                    tui.post_log_update(None, "CRITICAL", f"FATAL: Config watcher failed to start: {e}")

            log.info("Starting event processor task.")
            processor_task = asyncio.create_task(self.event_processor.run())
            await processor_task

        except asyncio.CancelledError:
            log.warning("Orchestrator task was cancelled.")
        except Exception:
            log.critical("Orchestrator run failed with an unhandled exception.", exc_info=True)
        finally:
            log.info("Orchestrator entering cleanup phase.")
            if processor_task and not processor_task.done():
                processor_task.cancel()

            if self.monitor_service and self.monitor_service.is_running:
                await self.monitor_service.stop()

            if self.config_observer and self.config_observer.is_alive():
                self.config_observer.stop()
                self.config_observer.join()

            log.info("Orchestrator cleanup complete.")

    def pause_monitoring(self) -> None:
        log.info("Pausing all event processing.")
        self._is_paused = True
        for state in self.repo_states.values():
            if not state.is_stopped:
                state.is_paused = True
                state._update_display_emoji()
        self._post_tui_state_update()

    def suspend_monitoring(self) -> None:
        log.warning("Suspending filesystem monitoring service.")
        if self.monitor_service and self.monitor_service.is_running:
            asyncio.create_task(self.monitor_service.stop())  # noqa: RUF006

    def resume_monitoring(self) -> None:
        log.info("Resuming event processing and monitoring.")
        self._is_paused = False

        if self.config and self.monitor_service and not self.monitor_service.is_running:
            log.info("Restarting suspended monitoring service...")
            tui = TUIInterface(self.app)
            enabled_repos = [
                repo_id for repo_id, repo in self.config.repositories.items() if repo.enabled and repo._path_valid
            ]
            self.monitor_service = self._setup_monitoring(self.config, enabled_repos, tui)
            if self.monitor_service:
                self.monitor_service.start()

        for state in self.repo_states.values():
            if state.is_paused:
                state.is_paused = False
                state._update_display_emoji()
        self._post_tui_state_update()

    def setup_config_watcher(self, tui: TUIInterface) -> None:
        loop = asyncio.get_running_loop()

        class ConfigChangeHandler(FileSystemEventHandler):
            def __init__(self, orchestrator_ref: "WatchOrchestrator"):
                self._orchestrator = orchestrator_ref
                self._config_path_str = str(self._orchestrator.config_path.resolve())

            def on_modified(self, event: FileSystemEvent):
                if str(Path(event.src_path).resolve()) == self._config_path_str:
                    log.info("Configuration file modified, queueing reload event.")
                    monitored_event = MonitoredEvent(
                        repo_id="__config__", event_type="modified", src_path=self._orchestrator.config_path, is_directory=False
                    )
                    loop.call_soon_threadsafe(self._orchestrator.event_queue.put_nowait, monitored_event)

        try:
            self.config_observer = Observer()
            handler = ConfigChangeHandler(self)
            watch_dir = str(self.config_path.parent)
            self.config_observer.schedule(handler, watch_dir, recursive=False)
            log.info("Configuration file watcher scheduled", path=watch_dir)
            tui.post_log_update(None, "DEBUG", f"Watching config in: {watch_dir}")
        except Exception as e:
            log.error("Failed to set up configuration file watcher", error=str(e), exc_info=True)
            self.config_observer = None

    def toggle_repository_pause(self, repo_id: str) -> bool:
        repo_state = self.repo_states.get(repo_id)
        if not repo_state:
            log.warning("Attempted to toggle pause on non-existent repo state", repo_id=repo_id)
            return False

        repo_state.is_paused = not repo_state.is_paused
        repo_state._update_display_emoji()
        log.info("Toggled repository pause state", repo_id=repo_id, paused=repo_state.is_paused)
        return True

    async def toggle_repository_stop(self, repo_id: str) -> bool:
        repo_state = self.repo_states.get(repo_id)
        if not self.config or not (repo_config := self.config.repositories.get(repo_id)):
            log.warning("Attempted to toggle stop on non-existent repo config", repo_id=repo_id)
            return False

        if not repo_state:
            log.warning("Attempted to toggle stop on non-existent repo state", repo_id=repo_id)
            return False

        repo_state.is_stopped = not repo_state.is_stopped

        if repo_state.is_stopped:
            log.info("Stopping monitoring for repository", repo_id=repo_id)
            if self.monitor_service:
                self.monitor_service.unschedule_repository(repo_id)
        else:
            log.info("Resuming monitoring for stopped repository", repo_id=repo_id)
            if self.monitor_service:
                try:
                    loop = asyncio.get_running_loop()
                    self.monitor_service.add_repository(repo_id, repo_config, loop)
                    if repo_state.status == RepositoryStatus.ERROR:
                        repo_state.reset_after_action()
                except Exception as e:
                    log.error("Failed to re-add repository to monitor", repo_id=repo_id, error=str(e), exc_info=True)
                    repo_state.update_status(RepositoryStatus.ERROR, f"Failed to resume monitoring: {e}")
                    repo_state.is_stopped = True
                    return False

        repo_state._update_display_emoji()
        self._post_tui_state_update()
        return True

    def _post_tui_state_update(self):
        if self.app:
            tui = TUIInterface(self.app)
            tui.post_state_update(self.repo_states)

    async def reload_config(self) -> bool:
        log.info("Reloading configuration...")
        self._is_paused = True
        tui = TUIInterface(self.app)
        tui.post_log_update(None, "INFO", "Pausing all monitoring for config reload...")
        self._post_tui_state_update()

        if self.monitor_service and self.monitor_service.is_running:
            await self.monitor_service.stop()
            self.monitor_service.clear_handlers()

        try:
            await asyncio.sleep(1)
            new_config = await asyncio.to_thread(load_config, self.config_path)
            self.config = new_config
            if self.event_processor:
                self.event_processor.config = new_config

            self.repo_states.clear()
            self.repo_engines.clear()
            enabled_repos = await self._initialize_repositories(self.config, tui)

            if not enabled_repos:
                log.warning("No enabled repositories after reload.")
                tui.post_log_update(None, "WARNING", "Config reloaded, but no repositories are enabled.")
                return True

            self.monitor_service = self._setup_monitoring(self.config, enabled_repos, tui)
            if self.monitor_service:
                self.monitor_service.start()
                tui.post_log_update(None, "INFO", "Monitoring resumed with new configuration.")

            log.info("Configuration reloaded and monitoring restarted.")
            return True
        except ConfigurationError as e:
            log.error("Failed to reload configuration", error=str(e), exc_info=True)
            tui.post_log_update(None, "ERROR", f"Config reload failed: {e}")
            return False
        finally:
            self._is_paused = False
            self._post_tui_state_update()

    async def _initialize_repositories(self, config: SupsrcConfig, tui: TUIInterface) -> list[str]:
        log.info("Initializing repositories...")
        tui.post_log_update(None, "INFO", "Initializing repositories...")
        enabled_repo_ids = []

        for repo_id, repo_config in config.repositories.items():
            init_log = log.bind(repo_id=repo_id)
            if not repo_config.enabled or not repo_config._path_valid:
                init_log.info("Skipping disabled/invalid repo")
                continue

            repo_state = RepositoryState(repo_id=repo_id)
            self.repo_states[repo_id] = repo_state

            try:
                engine_type = repo_config.repository.get("type", "supsrc.engines.git")
                init_log.debug("Attempting to load engine", engine_type=engine_type)
                if engine_type == "supsrc.engines.git":
                    self.repo_engines[repo_id] = GitEngine()
                else:
                    raise NotImplementedError(f"Engine '{engine_type}' not supported.")
                init_log.debug("Engine loaded successfully")

                rule_type_str = getattr(repo_config.rule, "type", "default")
                repo_state.rule_emoji = RULE_EMOJI_MAP.get(rule_type_str, RULE_EMOJI_MAP["default"])
                repo_state.rule_dynamic_indicator = rule_type_str.split(".")[-1].replace("_", " ").capitalize()

                engine = self.repo_engines[repo_id]
                if hasattr(engine, "get_summary"):
                    init_log.debug("Getting initial repository summary")
                    summary = cast(GitRepoSummary, await engine.get_summary(repo_config.path))
                    if summary.head_commit_hash:
                        repo_state.last_commit_short_hash = summary.head_commit_hash[:7]
                        repo_state.last_commit_message_summary = summary.head_commit_message_summary
                        msg = f"HEAD at {summary.head_ref_name} ({repo_state.last_commit_short_hash})"
                        init_log.info(msg)
                        tui.post_log_update(repo_id, "INFO", msg)
                    elif summary.is_empty or summary.head_ref_name == "UNBORN":
                        init_log.info("Repo is empty or unborn.")
                        tui.post_log_update(repo_id, "INFO", "Repo is empty or unborn.")
                    elif summary.head_ref_name == "ERROR":
                        init_log.warning("Failed to get repo summary.", details=summary.head_commit_message_summary)
                        repo_state.update_status(RepositoryStatus.ERROR, f"Init failed: {summary.head_commit_message_summary}")
                    else:
                        init_log.warning("Could not determine initial HEAD commit.", summary_details=summary)

                enabled_repo_ids.append(repo_id)
            except Exception as e:
                init_log.error("Failed to initialize repository", error=str(e), exc_info=True)
                repo_state.update_status(RepositoryStatus.ERROR, f"Initialization failed: {e}")
                continue

        tui.post_state_update(self.repo_states)
        log.info(f"Initialized {len(enabled_repo_ids)} repositories.")
        return enabled_repo_ids

    def _setup_monitoring(self, config: SupsrcConfig, enabled_repo_ids: list[str], tui: TUIInterface) -> MonitoringService | None:
        if not enabled_repo_ids:
            return None

        log.info("Setting up filesystem monitoring...")
        tui.post_log_update(None, "INFO", "Setting up filesystem monitoring...")
        service = MonitoringService(self.event_queue)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.critical("Cannot get running event loop during monitoring setup.")
            return None

        for repo_id in enabled_repo_ids:
            try:
                service.add_repository(repo_id, config.repositories[repo_id], loop)
            except MonitoringSetupError as e:
                log.error("Failed to add repo to monitor", repo_id=repo_id, error=str(e))
                if self.repo_states.get(repo_id):
                    self.repo_states[repo_id].update_status(RepositoryStatus.ERROR, "Monitor setup failed")
                    tui.post_log_update(repo_id, "ERROR", f"Monitoring setup failed: {e}")

        tui.post_state_update(self.repo_states)
        return service

    async def resume_repository_monitoring(self, repo_id: str) -> bool:
        repo_state = self.repo_states.get(repo_id)
        if not repo_state:
            return False

        if repo_state.is_paused:
            repo_state.is_paused = False
            repo_state.pause_until = None
            log.info(f"Repository {repo_id} unpaused.")

        if repo_state.is_stopped:
            success = await self.toggle_repository_stop(repo_id)
            if not success:
                log.error(f"Failed to unstop {repo_id} during resume.")
                return False
            log.info(f"Repository {repo_id} unstopped.")

        self._post_tui_state_update()
        return True

    async def get_repository_details(self, repo_id: str) -> dict[str, Any]:
        log.debug("Fetching repository details for TUI", repo_id=repo_id)
        repo_engine = self.repo_engines.get(repo_id)
        repo_config = self.config.repositories.get(repo_id) if self.config else None

        if not repo_engine or not repo_config:
            return {"error": "Repository data not found."}

        if isinstance(repo_engine, GitEngine) and hasattr(repo_engine, "get_commit_history"):
            try:
                history = await repo_engine.get_commit_history(repo_config.path, limit=20)
                return {"commit_history": history}
            except Exception as e:
                log.error("Failed to get commit history from engine", repo_id=repo_id, error=str(e))
                return {"commit_history": [f"[bold red]Error fetching history: {e}[/]"]}

        return {"commit_history": ["Details not available for this engine type."]}


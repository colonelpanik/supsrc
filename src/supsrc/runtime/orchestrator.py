#
# src/supsrc/runtime/orchestrator.py
#

import asyncio
import time  # Import time for unique task names
from contextlib import suppress  # For cleaner task cancellation handling
from pathlib import Path
from typing import Any, Optional, TypeAlias, cast

import attrs
import structlog
from rich.console import Console

from supsrc.config import (
    InactivityRuleConfig,
    RuleConfig,
    SupsrcConfig,
    load_config,
)

# --- Specific Engine Import (replace with plugin loading later) ---
from supsrc.engines.git import GitEngine, GitRepoSummary  # Import summary class too
from supsrc.exceptions import MonitoringSetupError, SupsrcError
from supsrc.llm import get_llm_provider
from supsrc.monitor import MonitoredEvent, MonitoringService

# --- Import concrete result types and base protocols ---
from supsrc.protocols import (
    CommitResult,
    PushResult,
    RepositoryEngine,
    RepoStatusResult,  # Concrete result classes
    StageResult,
)
from supsrc.rules import check_trigger_condition
from supsrc.state import RepositoryState, RepositoryStatus

# --- Supsrc Imports ---
from supsrc.telemetry import StructLogger
from supsrc.testing import LlmTestAnalyzer, get_test_runner

# --- TUI Integration Imports (Conditional) ---
try:
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from textual.app import App as TextualApp
    from supsrc.tui.app import LogMessageUpdate, StateUpdate
    TEXTUAL_AVAILABLE_RUNTIME = True
except ImportError:
    TEXTUAL_AVAILABLE_RUNTIME = False
    TextualApp = None # type: ignore
    StateUpdate = None # type: ignore
    LogMessageUpdate = None # type: ignore

# Logger for this module
log: StructLogger = structlog.get_logger("runtime.orchestrator")

# --- Rule Type to Emoji Mapping ---
RULE_EMOJI_MAP = {
    "supsrc.rules.inactivity": "⏳",
    "supsrc.rules.save_count": "💾",
    "supsrc.rules.manual": "✋",
    "default": "⚙️", # Fallback
}


# Type alias for state map
RepositoryStatesMap: TypeAlias = dict[str, RepositoryState]

class WatchOrchestrator:
    """
    Manages the core watch lifecycle, coordinating monitoring, state, rules,
    engines, and optionally updating a Textual TUI.
    """

    def __init__(
        self,
        config_path: Path,
        shutdown_event: asyncio.Event,
        app: Optional["TextualApp"] = None, # Accept optional TUI app instance
        console: Console | None = None
        ) -> None:
        """
        Initializes the orchestrator.

        Args:
            config_path: Path to the configuration file.
            shutdown_event: Event signalling graceful shutdown.
            app: Optional instance of the Textual TUI application.
            console: Optional instance of Rich Console for non-TUI output.
        """
        self.config_path = config_path
        self.shutdown_event = shutdown_event
        self.app: TextualApp | None = app if TEXTUAL_AVAILABLE_RUNTIME else None # Store the TUI app instance only if usable
        self.console = console
        self.config: SupsrcConfig | None = None
        self.monitor_service: MonitoringService | None = None
        self.event_queue: asyncio.Queue[MonitoredEvent] = asyncio.Queue()
        self.repo_states: RepositoryStatesMap = {}
        self.repo_engines: dict[str, RepositoryEngine] = {}
        self.repo_test_analyzers: dict[str, LlmTestAnalyzer] = {}
        self._running_tasks: set[asyncio.Task[Any]] = set()
        self._log = log.bind(orchestrator_id=id(self))
        self._is_tui_active = bool(self.app) # Flag for easier checking

    # --- Console and TUI Update Helpers ---

    def _console_message(self, message: str, repo_id: str | None = None, style: str | None = None, emoji: str | None = None) -> None:
        """Helper to print messages to the Rich console if available (non-TUI mode)."""
        if self.console and not self._is_tui_active: # Only print if console exists and not in TUI mode
            formatted_message = message
            if repo_id:
                # Using a consistent repo_id style for console messages
                formatted_message = f"[bold blue]{repo_id}[/]: {formatted_message}"
            if emoji:
                formatted_message = f"{emoji} {formatted_message}"
            self.console.print(formatted_message, style=style if style else None)

    def _post_tui_log(self, repo_id: str | None, level: str, message: str) -> None:
        """Safely posts a log message to the TUI if active."""
        if self._is_tui_active and self.app and LogMessageUpdate:
            try:
                # Use call_later for thread safety from worker
                self.app.call_later(self.app.post_message, LogMessageUpdate(repo_id, level.upper(), message))
            except Exception as e:
                 self._safe_log("warning", "Failed to post log message to TUI", repo_id=repo_id, error=str(e))

    def _post_tui_state_update(self) -> None:
        """Safely posts the current repository states to the TUI."""
        if self._is_tui_active and self.app and StateUpdate:
            try:
                # Create a copy for thread safety/mutability concerns
                states_copy = {rid: attrs.evolve(state) for rid, state in self.repo_states.items()}
                # Directly post the message without call_later as post_message is thread-safe
                self.app.post_message(StateUpdate(states_copy))
            except Exception as e:
                 self._safe_log("warning", "Failed to post state update to TUI", error=str(e))

    # --- Core Logic Methods ---

    async def _trigger_action_callback(self, repo_id: str) -> None:
        """
        Callback executed when a trigger condition is met. Executes repository actions.
        """
        repo_state = self.repo_states.get(repo_id)
        repo_config = self.config.repositories.get(repo_id) if self.config else None
        global_config = self.config.global_config if self.config else None
        repo_engine = self.repo_engines.get(repo_id)

        callback_log = self._log.bind(repo_id=repo_id)
        callback_log.debug("Entering action callback")

        if not repo_state or not repo_config or not global_config or not repo_engine:
            callback_log.error("Action Triggered: Could not find state, config, or engine.")
            return

        if repo_state.status not in (RepositoryStatus.CHANGED, RepositoryStatus.IDLE):
            callback_log.warning("Action Triggered: Repo not in CHANGED/IDLE state, skipping.",
                                current_status=repo_state.status.name)
            repo_state.cancel_inactivity_timer()
            return

        repo_state.update_status(RepositoryStatus.TRIGGERED)
        repo_state.cancel_inactivity_timer()
        self._post_tui_state_update()

        rule_config_obj: RuleConfig = repo_config.rule
        rule_type_str = getattr(rule_config_obj, "type", "unknown_rule_type")

        repo_state.action_description = "Queued..."
        repo_state.rule_dynamic_indicator = "Triggered!"
        self._console_message("Rule triggered: Action sequence started.", repo_id=repo_id, style="green bold", emoji="✅")
        self._post_tui_state_update()
        callback_log.info(
            "Action Triggered: Performing actions...",
            rule_type=rule_type_str,
            current_save_count=repo_state.save_count
        )

        engine_config_dict = repo_config.repository
        working_dir = repo_config.path

        try:
            repo_state.update_status(RepositoryStatus.PROCESSING)
            repo_state.action_description = "Checking status..."
            self._console_message("Checking repository status...", repo_id=repo_id, style="blue bold", emoji="🔄")
            self._post_tui_state_update()

            status_result: RepoStatusResult = await repo_engine.get_status(repo_state, engine_config_dict, global_config, working_dir)
            if not status_result.success:
                raise SupsrcError(f"Failed to get repository status: {status_result.message}")

            if status_result.is_conflicted:
                raise SupsrcError("Action Skipped: Repository has conflicts.")

            if status_result.is_clean:
                callback_log.info("Action Skipped: Repository is clean, no action needed.")
                repo_state.action_description = "Skipped (clean)"
                self._console_message("Action skipped: Repository clean.", repo_id=repo_id, style="dim", emoji="🚫")
                repo_state.reset_after_action()
                self._post_tui_state_update()
                return

            repo_state.update_status(RepositoryStatus.STAGING)
            repo_state.action_description = "Staging changes..."
            self._console_message("Staging changes...", repo_id=repo_id, style="blue bold", emoji="🔄")
            self._post_tui_state_update()

            stage_result: StageResult = await repo_engine.stage_changes(None, repo_state, engine_config_dict, global_config, working_dir)
            if not stage_result.success:
                raise SupsrcError(f"Failed to stage changes: {stage_result.message}")

            staged_files_list = stage_result.files_staged or []

            llm_analyzer = self.repo_test_analyzers.get(repo_id)
            if llm_analyzer:
                # --- LLM-driven Test & Commit Workflow ---
                callback_log.info("Executing LLM-driven test and analysis workflow.")
                repo_state.action_description = "Running tests..."
                self._console_message("Running tests...", repo_id=repo_id, style="blue bold", emoji="🧪")
                self._post_tui_state_update()

                staged_diff = await repo_engine.get_staged_diff(working_dir)
                test_config = repo_config.testing
                command = test_config.command or [test_config.runner]

                analysis_result = await llm_analyzer.analyze(
                    staged_diff=staged_diff,
                    staged_files=staged_files_list,
                    command=command,
                    repo_path=working_dir
                )

                if analysis_result.is_safe_to_commit:
                    callback_log.info("LLM analysis determined changes are safe to commit.", suggestion=analysis_result.suggestion)
                    repo_state.action_description = "Tests passed. Committing..."
                    self._console_message("Tests passed, committing with AI suggestion...", repo_id=repo_id, style="blue bold", emoji="💾")
                    self._post_tui_state_update()

                    commit_result = await repo_engine.perform_commit(
                        analysis_result.suggestion, # Use AI suggestion as the commit message
                        repo_state, engine_config_dict, global_config, working_dir
                    )
                    if not commit_result.success:
                        raise SupsrcError(f"Commit failed after successful test run: {commit_result.message}")

                    repo_state.last_commit_short_hash = commit_result.commit_hash[:7] if commit_result.commit_hash else "N/A"
                    repo_state.last_commit_message_summary = analysis_result.suggestion.split("\n")[0]
                    self._console_message(f"Commit successful: {repo_state.last_commit_short_hash}", repo_id=repo_id, style="green bold", emoji="✅")
                else:
                    callback_log.error("LLM analysis determined changes are NOT safe to commit.", diagnosis=analysis_result.suggestion)
                    raise SupsrcError(f"Tests failed. AI Diagnosis: {analysis_result.suggestion}")

            else:
                # --- Legacy Commit Workflow ---
                callback_log.info("Executing legacy commit workflow (no LLM/testing config).")
                repo_state.update_status(RepositoryStatus.COMMITTING)
                repo_state.action_description = "Committing..."
                self._console_message("Performing commit...", repo_id=repo_id, style="blue bold", emoji="🔄")
                self._post_tui_state_update()

                commit_message_template = "feat: auto-commit changes at {{timestamp}}\n\n{{change_summary}}"

                commit_result: CommitResult = await repo_engine.perform_commit(
                    commit_message_template, repo_state, engine_config_dict, global_config, working_dir
                )
                if not commit_result.success:
                    raise SupsrcError(f"Commit failed: {commit_result.message}")
                if commit_result.commit_hash:
                    repo_state.last_commit_short_hash = commit_result.commit_hash[:7]
                    self._console_message(f"Commit complete. Hash: {repo_state.last_commit_short_hash}", repo_id=repo_id, style="green bold", emoji="✅")
                else:
                    self._console_message("Commit skipped: No changes after staging.", repo_id=repo_id, style="dim", emoji="🚫")

            # --- Push Operation (Common to both successful workflows) ---
            repo_state.update_status(RepositoryStatus.PUSHING)
            repo_state.action_description = "Pushing..."
            self._console_message("Pushing changes...", repo_id=repo_id, style="blue bold", emoji="🔄")
            self._post_tui_state_update()

            push_result: PushResult = await repo_engine.perform_push(repo_state, engine_config_dict, global_config, working_dir)
            if not push_result.success:
                raise SupsrcError(f"Push failed: {push_result.message}")

            if push_result.skipped:
                self._console_message("Push skipped (disabled in config).", repo_id=repo_id, style="dim", emoji="🚫")
            else:
                self._console_message("Push successful.", repo_id=repo_id, style="green bold", emoji="✅")

            repo_state.reset_after_action()
            self._post_tui_state_update()

        except Exception as action_exc:
            error_message = str(action_exc)
            repo_state.action_description = f"Error: {error_message[:40]}..."
            self._console_message(f"Action failed: {error_message}", repo_id=repo_id, style="red bold", emoji="❌")
            callback_log.error("Action Failed: Error during execution", error=error_message, exc_info=True)
            if repo_state:
                 repo_state.update_status(RepositoryStatus.ERROR, error_message)
                 self._post_tui_state_update()

    async def _consume_events(self) -> None:
        """Consumes events from the queue, updates state, manages timers, checks rules."""
        consumer_log = self._log.bind(component="EventConsumer")
        consumer_log.info("Event consumer starting loop.")
        processed_event_count = 0

        while not self.shutdown_event.is_set():
            event: MonitoredEvent | None = None
            repo_id: str | None = None
            get_task: asyncio.Task | None = None
            shutdown_wait_task: asyncio.Task | None = None

            try:
                get_task = asyncio.create_task(self.event_queue.get(), name=f"QueueGet-{processed_event_count}")
                shutdown_wait_task = asyncio.create_task(self.shutdown_event.wait(), name=f"ShutdownWait-{processed_event_count}")

                done, pending = await asyncio.wait({get_task, shutdown_wait_task}, return_when=asyncio.FIRST_COMPLETED)

                if shutdown_wait_task in done or self.shutdown_event.is_set():
                    consumer_log.info("Consumer detected shutdown while waiting.")
                    if get_task in pending:
                        get_task.cancel()
                    break

                if get_task in done:
                    event = get_task.result()
                    repo_id = event.repo_id
                else:
                    continue

                if shutdown_wait_task in pending:
                    shutdown_wait_task.cancel()

                processed_event_count += 1
                event_log = consumer_log.bind(repo_id=repo_id, event_type=event.event_type, src_path=str(event.src_path))

                repo_state = self.repo_states.get(repo_id)
                repo_config = self.config.repositories.get(repo_id) if self.config else None

                if not repo_state or not repo_config:
                    event_log.warning("Ignoring event for unknown/disabled/unconfigured repository.")
                    continue

                if repo_state.status not in (RepositoryStatus.IDLE, RepositoryStatus.CHANGED):
                    event_log.debug(
                        "Ignoring event: an action is already in progress for this repository.",
                        current_status=repo_state.status.name
                    )
                    continue

                event_log.debug("Processing received event")
                repo_state.record_change()
                self._console_message(f"Change detected: {event.src_path.name}", repo_id=repo_id, style="magenta bold", emoji="✏️")

                rule_config_obj: RuleConfig = repo_config.rule
                rule_type_str = getattr(rule_config_obj, "type", "default")
                repo_state.rule_emoji = RULE_EMOJI_MAP.get(rule_type_str, RULE_EMOJI_MAP["default"])

                self._post_tui_state_update()

                rule_met = check_trigger_condition(repo_state, repo_config)
                if rule_met:
                    action_task = asyncio.create_task(self._trigger_action_callback(repo_id), name=f"Action-{repo_id}-{time.monotonic()}")
                    self._running_tasks.add(action_task)
                    action_task.add_done_callback(self._running_tasks.discard)
                elif isinstance(rule_config_obj, InactivityRuleConfig):
                    delay = rule_config_obj.period.total_seconds()
                    self._console_message(f"Waiting for inactivity period ({delay:.0f}s)...", repo_id=repo_id, style="italic yellow", emoji="⏳")
                    current_loop = asyncio.get_running_loop()
                    timer_handle = current_loop.call_later(
                        delay, lambda rid=repo_id: asyncio.create_task(self._trigger_action_callback(rid))
                    )
                    repo_state.set_inactivity_timer(timer_handle)

            except asyncio.CancelledError:
                consumer_log.info("Consumer task processing cancelled. Cleaning up internal tasks...")
                if get_task and not get_task.done():
                    get_task.cancel()
                if shutdown_wait_task and not shutdown_wait_task.done():
                    shutdown_wait_task.cancel()
                break

            except Exception as e:
                current_repo_id = repo_id if repo_id else "UNKNOWN"
                consumer_log.error("Error in consumer loop", repo_id=current_repo_id, error=str(e), exc_info=True)
            finally:
                if event:
                    with suppress(ValueError, Exception):
                        self.event_queue.task_done()

        consumer_log.info(f"Event consumer finished after processing {processed_event_count} events.")


    async def _initialize_repositories(self) -> list[str]:
        """Initializes states, loads engines, and other components."""
        enabled_repo_ids = []
        if not self.config:
            self._safe_log("error", "Config missing, cannot initialize repos.")
            return []

        self._safe_log("info", "--- Initializing Repositories ---")
        for repo_id, repo_config in self.config.repositories.items():
            init_log = self._log.bind(repo_id=repo_id)
            if not repo_config.enabled or not repo_config._path_valid:
                init_log.info("Skipping initialization (disabled or invalid path)")
                continue

            try:
                repo_state = RepositoryState(repo_id=repo_id)
                self.repo_states[repo_id] = repo_state

                engine_config = repo_config.repository
                engine_type = engine_config.get("type")
                if engine_type == "supsrc.engines.git":
                    engine_instance = GitEngine()
                else:
                    raise NotImplementedError(f"Engine type '{engine_type}' not supported.")
                self.repo_engines[repo_id] = engine_instance
                init_log.debug("Engine loaded", engine_class=type(engine_instance).__name__)

                if repo_config.llm and repo_config.testing:
                    init_log.info("LLM and testing config found, setting up analyzer.")
                    try:
                        llm_provider = get_llm_provider(repo_config.llm)
                        test_runner = get_test_runner(repo_config.testing.runner)
                        analyzer = LlmTestAnalyzer(runner=test_runner, llm_provider=llm_provider)
                        self.repo_test_analyzers[repo_id] = analyzer
                        init_log.info("LlmTestAnalyzer successfully initialized.")
                    except Exception as analyzer_exc:
                        init_log.error("Failed to initialize LlmTestAnalyzer", error=str(analyzer_exc), exc_info=True)
                        repo_state.update_status(RepositoryStatus.ERROR, f"LLM/Test setup failed: {analyzer_exc}")
                        continue

                if hasattr(engine_instance, "get_summary"):
                    summary = cast(GitRepoSummary, await engine_instance.get_summary(repo_config.path))
                    if summary.head_commit_hash:
                        repo_state.last_commit_short_hash = summary.head_commit_hash[:7]
                    repo_state.last_commit_message_summary = summary.head_commit_message_summary
                    self._console_message(f"Watching: {repo_config.path} (Branch: {summary.head_ref_name})", repo_id=repo_id, style="dim", emoji="📂")

                enabled_repo_ids.append(repo_id)

            except Exception as e:
                init_log.error("Failed to initialize repository", error=str(e), exc_info=True)
                if repo_id in self.repo_states:
                    self.repo_states[repo_id].update_status(RepositoryStatus.ERROR, f"Initialization failed: {e}")

        self._post_tui_state_update()
        return enabled_repo_ids

    def _setup_monitoring(self, enabled_repo_ids: list[str]) -> list[str]:
        """Adds successfully initialized repositories to the MonitoringService."""
        successfully_added_ids = []
        if not self.config: return []
        self.monitor_service = MonitoringService(self.event_queue)
        try:
            loop = asyncio.get_running_loop()
            for repo_id in enabled_repo_ids:
                repo_config = self.config.repositories[repo_id]
                try:
                    self.monitor_service.add_repository(repo_id, repo_config, loop)
                    successfully_added_ids.append(repo_id)
                except MonitoringSetupError as e:
                    self._log.error("Failed to setup monitoring", repo_id=repo_id, error=str(e))
                    if repo_id in self.repo_states:
                        self.repo_states[repo_id].update_status(RepositoryStatus.ERROR, f"Monitor setup failed: {e}")
            return successfully_added_ids
        except Exception as e:
            self._log.critical("Critical error during monitoring setup", error=str(e))
            return []


    async def run(self) -> None:
        """Main execution method for the watch process."""
        self._safe_log("info", "Starting orchestrator run", config_path=str(self.config_path))
        try:
            self.config = load_config(self.config_path)
            enabled_repo_ids = await self._initialize_repositories()
            if not enabled_repo_ids: return

            successfully_added_ids = self._setup_monitoring(enabled_repo_ids)
            if not successfully_added_ids or not self.monitor_service: return

            self.monitor_service.start()
            if not self.monitor_service.is_running:
                raise SupsrcError("Monitor service failed to start.")

            consumer_task = asyncio.create_task(self._consume_events(), name="EventConsumer")
            self._running_tasks.add(consumer_task)
            consumer_task.add_done_callback(self._running_tasks.discard)

            self._safe_log("info", "Orchestrator running. Waiting for shutdown signal.")
            await self.shutdown_event.wait()

        except Exception as e:
            self._safe_log("critical", "Orchestrator run failed", error=str(e), exc_info=True)
            self.shutdown_event.set()
        finally:
            self._safe_log("info", "Orchestrator starting cleanup.")
            tasks_to_cancel = list(self._running_tasks)
            if tasks_to_cancel:
                for task in tasks_to_cancel: task.cancel()
                await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

            if self.monitor_service and self.monitor_service.is_running:
                await self.monitor_service.stop()
            self._safe_log("info", "Orchestrator finished cleanup.")

    def _safe_log(self, level: str, msg: str, **kwargs):
        """Helper to suppress logging errors during final shutdown."""
        with suppress(Exception):
            getattr(self._log, level)(msg, **kwargs)

    async def get_repository_details(self, repo_id: str) -> dict[str, Any]:
        """Retrieves detailed information for a given repository."""
        repo_engine = self.repo_engines.get(repo_id)
        repo_config = self.config.repositories.get(repo_id) if self.config else None
        if isinstance(repo_engine, GitEngine) and repo_config:
            return {"commit_history": repo_engine.get_commit_history(repo_config.path)}
        return {"commit_history": ["Details not available for this engine type."]}

# 🔼⚙️

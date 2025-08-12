#
# supsrc/runtime/orchestrator.py
#

import asyncio
import time  # Import time for unique task names
from contextlib import suppress  # For cleaner task cancellation handling
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Optional, cast

import attrs
import cattrs  # Needed for config validation exceptions
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
from supsrc.exceptions import ConfigurationError, MonitoringSetupError, SupsrcError
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
from supsrc.state import RepositoryState, RepositoryStatus, STATUS_EMOJI_MAP

# --- Supsrc Imports ---
from supsrc.telemetry import StructLogger
from supsrc.types import RepositoryStatesMap

# --- TUI Integration Imports (Conditional) ---
try:
    from typing import TYPE_CHECKING

    from supsrc.tui.messages import LogMessageUpdate, StateUpdate

    TEXTUAL_AVAILABLE_RUNTIME = True

    if TYPE_CHECKING:
        from textual.app import App as TextualApp
    else:
        from textual.app import App as TextualApp
except ImportError:
    TEXTUAL_AVAILABLE_RUNTIME = False
    TextualApp = None  # type: ignore
    StateUpdate = None  # type: ignore
    LogMessageUpdate = None  # type: ignore

# Logger for this module
log: StructLogger = structlog.get_logger("runtime.orchestrator")

# --- Rule Type to Emoji Mapping ---
RULE_EMOJI_MAP = {
    "inactivity": "⏳",
    # Add other known rule type strings (lowercase) and their emojis
    # e.g., "filecount": "🗂️",
    # e.g., "savecount": "💾",
    "default": "⚙️",  # Fallback
}


class WatchOrchestrator:
    """
    Manages the core watch lifecycle, coordinating monitoring, state, rules,
    engines, and optionally updating a Textual TUI.
    """

    def __init__(
        self,
        config_path: Path,
        shutdown_event: asyncio.Event,
        app: Optional["TextualApp"] = None,  # Accept optional TUI app instance
        console: Console | None = None,
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
        self.app: TextualApp | None = (
            app if TEXTUAL_AVAILABLE_RUNTIME else None
        )  # Store the TUI app instance only if usable
        self.console = console
        self.config: SupsrcConfig | None = None
        self.monitor_service: MonitoringService | None = None
        self.event_queue: asyncio.Queue[MonitoredEvent] = asyncio.Queue()
        self.repo_states: RepositoryStatesMap = {}
        self.repo_engines: dict[str, RepositoryEngine] = {}
        self._running_tasks: set[asyncio.Task[Any]] = set()
        self._log = log.bind(orchestrator_id=id(self))
        self._is_tui_active = bool(self.app)  # Flag for easier checking
        self._is_paused = False
        self._is_suspended = False
        self._last_stats_refresh = {}  # Track last refresh time per repo
        self._recent_moves = {}  # Track recent move events for deduplication

    # --- Console and TUI Update Helpers ---

    def _console_message(
        self,
        message: str,
        repo_id: str | None = None,
        style: str | None = None,
        emoji: str | None = None,
    ) -> None:
        """Helper to print messages to the console if available (non-TUI mode)."""
        if not self._is_tui_active:  # Only print if not in TUI mode
            formatted_message = message
            if repo_id:
                # Using a consistent repo_id style for console messages
                if self.console:
                    formatted_message = f"[bold blue]{repo_id}[/]: {formatted_message}"
                else:
                    formatted_message = f"[{repo_id}] {formatted_message}"
            if emoji:
                formatted_message = f"{emoji} {formatted_message}"
            
            if self.console:
                # Use Rich console if available
                self.console.print(formatted_message, style=style if style else None)
            else:
                # Fall back to simple print for tail command
                print(formatted_message)

    def _post_tui_log(self, repo_id: str | None, level: str, message: str) -> None:
        """Safely posts a log message to the TUI if active."""
        if self._is_tui_active and self.app and LogMessageUpdate:
            try:
                # Use call_later for thread safety from worker
                self.app.call_later(
                    self.app.post_message,
                    LogMessageUpdate(repo_id, level.upper(), message),
                )
            except Exception as e:
                self._safe_log(
                    "warning",
                    "Failed to post log message to TUI",
                    repo_id=repo_id,
                    error=str(e),
                )

    def _post_tui_state_update(self) -> None:
        """Safely posts the current repository states to the TUI."""
        if self._is_tui_active and self.app and StateUpdate:
            self._safe_log("debug", "_post_tui_state_update called.")
            try:
                # Create a copy for thread safety/mutability concerns
                states_copy = {rid: attrs.evolve(state) for rid, state in self.repo_states.items()}
                self._safe_log(
                    "debug",
                    f"Posting state update to TUI with {len(states_copy)} repositories",
                )
                # Use call_later for thread safety from worker thread
                self.app.call_later(self.app.post_message, StateUpdate(states_copy))
            except Exception as e:
                self._safe_log("warning", "Failed to post state update to TUI", error=str(e))

    # --- Core Logic Methods ---

    async def _refresh_file_stats(self, repo_id: str) -> None:
        """
        Refresh file statistics for a repository without triggering UI flashing.
        """
        refresh_log = self._log.bind(repo_id=repo_id)
        refresh_log.debug("Refreshing file statistics")
        
        repo_state = self.repo_states.get(repo_id)
        repo_config = self.config.repositories.get(repo_id) if self.config else None
        repo_engine = self.repo_engines.get(repo_id)
        
        if not repo_state or not repo_config or not repo_engine:
            return
            
        try:
            # Add a small delay to batch multiple rapid changes
            await asyncio.sleep(1.0)
            
            # Get current status
            status_result = await repo_engine.get_status(
                repo_state,
                repo_config.repository,
                self.config.global_config if self.config else {},
                repo_config.path
            )
            
            if status_result.success:
                # Only update if values have changed
                if (repo_state.changed_files != status_result.changed_files or
                    repo_state.added_files != status_result.added_files or
                    repo_state.deleted_files != status_result.deleted_files or
                    repo_state.modified_files != status_result.modified_files):
                    
                    repo_state.changed_files = status_result.changed_files
                    repo_state.added_files = status_result.added_files
                    repo_state.deleted_files = status_result.deleted_files
                    repo_state.modified_files = status_result.modified_files
                    repo_state.has_uncommitted_changes = not status_result.is_clean
                    
                    # If repository is now clean, cancel any inactivity timer and update status
                    if status_result.is_clean and repo_state.inactivity_timer_handle:
                        refresh_log.info("Repository is now clean, canceling inactivity timer")
                        repo_state.cancel_inactivity_timer()
                        repo_state.update_status(RepositoryStatus.IDLE)
                        self._post_tui_log(
                            repo_id,
                            "INFO",
                            "⏰ Timer cancelled - repository is clean"
                        )
                    
                    self._last_stats_refresh[repo_id] = time.time()
                    self._post_tui_state_update()
                    
                    refresh_log.debug(
                        "File statistics updated",
                        changed=repo_state.changed_files,
                        added=repo_state.added_files,
                        deleted=repo_state.deleted_files,
                        modified=repo_state.modified_files
                    )
        except Exception as e:
            refresh_log.debug("Error refreshing file statistics", error=str(e))

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
            # self._post_tui_log(repo_id, "ERROR", "Action failed: Missing state/config/engine.") # Redundant
            return

        # Check status first
        # Allow triggering from IDLE (e.g., timer fired before any new change) or CHANGED
        if repo_state.is_paused:
            callback_log.info("Action Triggered: Repository is paused, skipping action.", repo_id=repo_id)
            self._post_tui_log(
                repo_id,
                "WARNING",
                "⏸️ Commit skipped - repository is paused"
            )
            repo_state.cancel_inactivity_timer()
            return

        if repo_state.status not in (RepositoryStatus.CHANGED, RepositoryStatus.IDLE):
            callback_log.warning(
                "Action Triggered: Repo not in CHANGED/IDLE state, skipping.",
                current_status=repo_state.status.name,
            )
            # self._post_tui_log(repo_id, "WARNING", f"Action skipped (state: {repo_state.status.name}).") # Redundant
            repo_state.cancel_inactivity_timer()
            return

        # Mark as triggered and clear timer immediately
        repo_state.update_status(RepositoryStatus.TRIGGERED)
        repo_state.cancel_inactivity_timer()
        self._post_tui_state_update()  # Update TUI status

        rule_config_obj: RuleConfig = repo_config.rule
        rule_type_str = getattr(rule_config_obj, "type", "unknown_rule_type")

        # repo_state.active_rule_description = f"Action for {rule_type_str}" # Replaced by action_description
        repo_state.action_description = "Queued..."
        repo_state.rule_dynamic_indicator = "Triggered!"
        self._console_message(
            "Rule triggered: Commit due.",
            repo_id=repo_id,
            style="green bold",
            emoji="✅",
        )
        self._post_tui_state_update()  # Update TUI for triggered state
        callback_log.info(
            "Action Triggered: Performing actions...",
            rule_type=rule_type_str,
            current_save_count=repo_state.save_count,
        )
        # self._post_tui_log(repo_id, "INFO", f"Action triggered by rule: {rule_type_str}") # Redundant

        engine_config_dict = repo_config.repository  # This is the dict for the engine
        working_dir = repo_config.path

        try:
            # --- 1. Get Status ---
            repo_state.update_status(
                RepositoryStatus.PROCESSING
            )  # Emoji will be set by update_status
            repo_state.action_description = "Checking status..."
            repo_state.action_progress_total = None
            repo_state.action_progress_completed = None
            self._console_message(
                "Checking repository status...",
                repo_id=repo_id,
                style="blue bold",
                emoji="🔄",
            )
            # self._post_tui_log(repo_id, "DEBUG", "Checking repository status...") # Redundant
            self._post_tui_state_update()

            status_result: RepoStatusResult = await repo_engine.get_status(
                repo_state, engine_config_dict, global_config, working_dir
            )
            repo_state.action_description = "Status OK"  # Or more specific if needed
            self._post_tui_state_update()
            callback_log.debug(
                "Received status result from engine",
                status_result=attrs.asdict(status_result),
            )

            if not status_result.success:
                raise SupsrcError(f"Failed to get repository status: {status_result.message}")
            
            # Update file statistics in repository state
            # Only update if values have actually changed to avoid UI flashing
            if (repo_state.total_files != status_result.total_files or
                repo_state.changed_files != status_result.changed_files or
                repo_state.added_files != status_result.added_files or
                repo_state.deleted_files != status_result.deleted_files or
                repo_state.modified_files != status_result.modified_files):
                
                repo_state.total_files = status_result.total_files
                repo_state.changed_files = status_result.changed_files
                repo_state.added_files = status_result.added_files
                repo_state.deleted_files = status_result.deleted_files
                repo_state.modified_files = status_result.modified_files
                repo_state.has_uncommitted_changes = not status_result.is_clean
                repo_state.current_branch = status_result.current_branch
                
                # Update last refresh time
                self._last_stats_refresh[repo_id] = time.time()

            if status_result.is_conflicted:
                callback_log.warning("Repository has merge conflicts. Auto-freezing repository.")
                self._console_message(
                    "⚠️ CONFLICT DETECTED! Repository frozen.",
                    repo_id=repo_id,
                    style="bold yellow on red",
                    emoji="🧊",
                )
                
                # Auto-freeze the repository
                repo_state.is_frozen = True
                repo_state.freeze_reason = "Merge conflicts detected"
                repo_state.display_status_emoji = "🧊"
                repo_state.action_description = "Frozen (conflicts)"
                repo_state.update_status(RepositoryStatus.ERROR, "Frozen: Merge conflicts")
                
                # Send macOS notification if available
                try:
                    import subprocess
                    subprocess.run([
                        "osascript", "-e",
                        f'display notification "Merge conflicts in {repo_id}" with title "Supsrc: Repository Frozen" sound name "Basso"'
                    ], check=False)
                except Exception:
                    pass  # Ignore notification errors
                    
                self._post_tui_state_update()
                return

            if status_result.is_clean and status_result.is_unborn:
                callback_log.info("Action Skipped: Unborn repository is clean, no commit needed.")
                repo_state.action_description = "Skipped (unborn & clean)"
                repo_state.action_progress_total = None
                repo_state.action_progress_completed = None
                repo_state.display_status_emoji = "🚫"
                self._console_message(
                    "Action skipped: Unborn repository clean.",
                    repo_id=repo_id,
                    style="dim",
                    emoji="🚫",
                )
                # self._post_tui_log(repo_id, "INFO", "Action skipped: Unborn repository clean.") # Redundant
                repo_state.reset_after_action()
                self._post_tui_state_update()
                return
            elif status_result.is_clean:
                callback_log.info("Action Skipped: Repository is clean, no commit needed.")
                repo_state.action_description = "Skipped (clean)"
                repo_state.action_progress_total = None
                repo_state.action_progress_completed = None
                repo_state.display_status_emoji = "🚫"
                self._console_message(
                    "Action skipped: Repository clean.",
                    repo_id=repo_id,
                    style="dim",
                    emoji="🚫",
                )
                # self._post_tui_log(repo_id, "INFO", "Action skipped: Repository clean.") # Redundant
                repo_state.reset_after_action()
                self._post_tui_state_update()
                return

            # --- 2. Stage Changes ---
            repo_state.update_status(RepositoryStatus.STAGING)
            repo_state.action_description = "Staging changes..."
            repo_state.action_progress_total = None
            repo_state.action_progress_completed = None
            self._console_message(
                "Staging changes...", repo_id=repo_id, style="blue bold", emoji="🔄"
            )
            # self._post_tui_log(repo_id, "INFO", "Staging changes...") # Redundant
            self._post_tui_state_update()

            stage_result: StageResult = await repo_engine.stage_changes(
                None, repo_state, engine_config_dict, global_config, working_dir
            )
            if not stage_result.success:
                raise SupsrcError(f"Failed to stage changes: {stage_result.message}")

            files_staged_count = len(stage_result.files_staged or [])
            repo_state.action_description = f"Staged {files_staged_count} file(s)"
            # Simulate progress for staging if desired, e.g., total = 1, completed = 1
            self._console_message(
                f"Staged {files_staged_count} file(s).",
                repo_id=repo_id,
                style="green bold",
                emoji="✅",
            )
            self._post_tui_state_update()
            callback_log.info("Action: Staging successful.", files_staged=stage_result.files_staged)
            # self._post_tui_log(repo_id, "DEBUG", f"Staging successful ({files_staged_count} files).") # Redundant

            # --- 3. Perform Commit ---
            repo_state.update_status(RepositoryStatus.COMMITTING)
            repo_state.action_description = "Committing..."
            repo_state.action_progress_total = None
            repo_state.action_progress_completed = None
            self._console_message(
                "Performing commit...", repo_id=repo_id, style="blue bold", emoji="🔄"
            )
            # self._post_tui_log(repo_id, "INFO", "Performing commit...") # Redundant
            self._post_tui_state_update()

            # Try commit with retry for race conditions
            commit_retries = 3
            commit_attempt = 0
            commit_result: CommitResult | None = None
            
            while commit_attempt < commit_retries:
                try:
                    commit_result = await repo_engine.perform_commit(
                        message_template="unused",
                        state=repo_state,
                        config=engine_config_dict,
                        global_config=global_config,
                        working_dir=working_dir,
                    )
                    break  # Success, exit retry loop
                except Exception as e:
                    commit_attempt += 1
                    error_msg = str(e)
                    
                    # Check if this is a race condition error
                    if "current tip is not the first parent" in error_msg and commit_attempt < commit_retries:
                        callback_log.warning(
                            f"Commit failed due to race condition, retrying ({commit_attempt}/{commit_retries})",
                            error=error_msg,
                            repo_id=repo_id,
                        )
                        self._console_message(
                            f"Repository HEAD changed, retrying commit ({commit_attempt}/{commit_retries})...",
                            repo_id=repo_id,
                            style="yellow",
                            emoji="⚠️",
                        )
                        # Brief delay before retry
                        await asyncio.sleep(0.5)
                        continue
                    else:
                        # Not a race condition or out of retries
                        raise
            
            if commit_result is None:
                raise SupsrcError("Failed to commit after multiple attempts")
                
            if not commit_result.success:
                raise SupsrcError(f"Commit failed: {commit_result.message}")

            if commit_result.commit_hash is None:
                repo_state.action_description = (
                    f"Commit skipped ({commit_result.message or 'no changes'})"
                )
                repo_state.action_progress_total = None
                repo_state.action_progress_completed = None
                repo_state.display_status_emoji = "🚫"
                self._console_message(
                    "Commit skipped: No changes after staging.",
                    repo_id=repo_id,
                    style="dim",
                    emoji="🚫",
                )
                callback_log.info(
                    "Action Skipped: Commit skipped by engine.",
                    reason=commit_result.message,
                )
                # self._post_tui_log(repo_id, "INFO", f"Commit skipped: {commit_result.message}") # Redundant
                repo_state.reset_after_action()
                self._post_tui_state_update()
                return
            else:
                repo_state.last_commit_short_hash = commit_result.commit_hash[:7]
                repo_state.action_description = f"Committed: {repo_state.last_commit_short_hash}"
                repo_state.display_status_emoji = "✅"
                # Update the commit timestamp to now since we just made a commit
                from datetime import datetime, UTC
                repo_state.last_commit_timestamp = datetime.now(UTC)
                self._console_message(
                    f"Commit complete. Hash: {repo_state.last_commit_short_hash}",
                    repo_id=repo_id,
                    style="green bold",
                    emoji="✅",
                )
                self._post_tui_state_update()
                callback_log.info("Action: Commit successful", hash=commit_result.commit_hash)
                # self._post_tui_log(repo_id, "SUCCESS", f"Commit successful: {repo_state.last_commit_short_hash}") # Redundant

            # --- 4. Perform Push ---
            repo_state.update_status(RepositoryStatus.PUSHING)
            repo_state.action_description = "Pushing..."
            repo_state.action_progress_total = None
            repo_state.action_progress_completed = None
            self._console_message(
                "Pushing changes...", repo_id=repo_id, style="blue bold", emoji="🔄"
            )
            # self._post_tui_log(repo_id, "INFO", "Performing push (if enabled)...") # Redundant
            self._post_tui_state_update()

            push_result: PushResult = await repo_engine.perform_push(
                repo_state, engine_config_dict, global_config, working_dir
            )

            if not push_result.success:
                repo_state.action_description = f"Push failed: {push_result.message}"
                self._console_message(
                    f"Push failed: {push_result.message}. See logs.",
                    repo_id=repo_id,
                    style="red bold",
                    emoji="❌",
                )
                callback_log.warning("Action: Push failed", reason=push_result.message)
                # self._post_tui_log(repo_id, "WARNING", f"Push failed: {push_result.message}") # Redundant
                repo_state.reset_after_action()
            else:
                if push_result.skipped:
                    repo_state.action_description = "Push skipped (config)"
                    repo_state.display_status_emoji = "🚫"
                    self._console_message(
                        "Push skipped (disabled in config).",
                        repo_id=repo_id,
                        style="dim",
                        emoji="🚫",
                    )
                    callback_log.info("Action: Push skipped by configuration.")
                    # self._post_tui_log(repo_id, "INFO", "Push skipped (disabled in config).") # Redundant
                else:
                    repo_state.action_description = "Push successful"
                    repo_state.display_status_emoji = "✅"
                    self._console_message(
                        "Push successful.",
                        repo_id=repo_id,
                        style="green bold",
                        emoji="✅",
                    )
                    callback_log.info("Action: Push successful.")
                    # self._post_tui_log(repo_id, "SUCCESS", "Push successful.") # Redundant

                # After successful/skipped push, finalize action description before reset
                repo_state.action_description = "Completed"
                repo_state.action_progress_total = None
                repo_state.action_progress_completed = None
                
                # Don't reset the change counts - just mark as committed
                # The numbers should stay visible but will be shown in grey
                # Adjust total files based on what was added/deleted
                if repo_state.added_files > 0 or repo_state.deleted_files > 0:
                    # Adjust the cached total instead of recalculating
                    repo_state.total_files = repo_state.total_files + repo_state.added_files - repo_state.deleted_files
                    callback_log.debug(
                        "Adjusted total file count",
                        previous=repo_state.total_files - repo_state.added_files + repo_state.deleted_files,
                        added=repo_state.added_files,
                        deleted=repo_state.deleted_files,
                        new_total=repo_state.total_files
                    )
                
                # Just mark as no uncommitted changes - the counts remain for grey display
                repo_state.has_uncommitted_changes = False
                
                # Update branch if needed
                try:
                    final_status = await repo_engine.get_status(
                        repo_state, engine_config_dict, global_config, working_dir
                    )
                    if final_status.success:
                        repo_state.current_branch = final_status.current_branch
                except Exception as e:
                    callback_log.debug("Could not update branch", error=str(e))
                
                self._post_tui_state_update()
                repo_state.reset_after_action()

            self._post_tui_state_update()  # Final TUI state update

        except Exception as action_exc:
            repo_state.action_description = f"Error: {str(action_exc)[:30]}..."
            repo_state.action_progress_total = None
            repo_state.action_progress_completed = None
            self._console_message(
                f"Action failed: {action_exc}. See logs for details.",
                repo_id=repo_id,
                style="red bold",
                emoji="❌",
            )
            callback_log.error(
                "Action Failed: Error during execution",
                error=str(action_exc),
                exc_info=True,
            )
            if repo_state:
                repo_state.update_status(RepositoryStatus.ERROR, f"Action failed: {action_exc}")
                # self._post_tui_log(repo_id, "ERROR", f"Action failed: {action_exc}") # Redundant
                self._post_tui_state_update()
            else:
                # self._post_tui_log(repo_id, "CRITICAL", f"Action failed (state missing): {action_exc}") # Redundant
                # If repo_state is None here, callback_log (which is self._log.bind(repo_id=repo_id))
                # will log with the repo_id, but not specific state error.
                # Consider if a general log is needed if repo_state itself is None.
                self._log.critical(
                    "Action failed and repo_state is None",
                    repo_id_if_known=repo_id,
                    root_error=str(action_exc),
                )

    async def _consume_events(self) -> None:
        """Consumes events from the queue, updates state, manages timers, checks rules."""
        consumer_log = self._log.bind(component="EventConsumer")
        consumer_log.info("Event consumer starting loop.")
        processed_event_count = 0

        while not self.shutdown_event.is_set():
            consumer_log.debug("Consumer loop active, waiting for event...")
            event: MonitoredEvent | None = None  # Keep default None
            repo_id: str | None = None  # Initialize repo_id outside try

            get_task: asyncio.Task | None = None
            shutdown_wait_task: asyncio.Task | None = None

            try:
                consumer_log.debug(
                    f"Consumer loop iteration. Processed: {processed_event_count}. Waiting on queue.get()...",
                    queue_id=id(self.event_queue),
                )
                # Create tasks for BOTH awaitables
                get_task = asyncio.create_task(
                    self.event_queue.get(),
                    name=f"QueueGet-{id(self.event_queue)}-{processed_event_count}",
                )
                shutdown_wait_task = asyncio.create_task(
                    self.shutdown_event.wait(),
                    name=f"ShutdownWait-{processed_event_count}",
                )

                # Wait for either task to complete
                done, pending = await asyncio.wait(
                    {get_task, shutdown_wait_task}, return_when=asyncio.FIRST_COMPLETED
                )

                # Prioritize checking shutdown task completion
                if shutdown_wait_task in done or self.shutdown_event.is_set():
                    consumer_log.info("Consumer detected shutdown while waiting.")
                    if get_task in pending:
                        get_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await get_task
                    if shutdown_wait_task in pending:
                        shutdown_wait_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await shutdown_wait_task
                    break  # Exit the while loop

                # If shutdown didn't happen, get_task must be in done
                if get_task in done:
                    event = get_task.result()
                    consumer_log.debug(">>> Consumer AWOKE from queue.get() with event.", event_type=event.event_type, src_path=str(event.src_path))
                    # Assign repo_id *before* the main processing try block
                    repo_id = event.repo_id
                else:
                    consumer_log.warning(
                        "asyncio.wait returned unexpectedly without queue item or shutdown signal."
                    )
                    continue

                # Cancel the shutdown_wait_task if the event was received
                if shutdown_wait_task in pending:
                    shutdown_wait_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await shutdown_wait_task

                # --- Check if paused ---
                if self._is_paused:
                    consumer_log.info(
                        "Monitoring is PAUSED, skipping event processing",
                        repo_id=repo_id,
                        event_type=event.event_type,
                    )
                    # Put the event back in the queue for later processing
                    await self.event_queue.put(event)
                    # Sleep a bit to avoid busy loop while paused
                    await asyncio.sleep(0.5)
                    continue

                # --- Check if repository is individually frozen/stopped (but not just paused) ---
                repo_state = self.repo_states.get(repo_id) if repo_id != "__config__" else None
                if repo_state and (repo_state.is_frozen or repo_state.is_stopped):
                    consumer_log.info(
                        "Repository is frozen/stopped, skipping event",
                        repo_id=repo_id,
                        is_frozen=repo_state.is_frozen,
                        is_stopped=repo_state.is_stopped,
                        freeze_reason=repo_state.freeze_reason,
                    )
                    # Put the event back in the queue for later processing
                    await self.event_queue.put(event)
                    await asyncio.sleep(0.5)
                    continue

                # --- Process Event ---
                processed_event_count += 1
                event_log = consumer_log.bind(
                    repo_id=repo_id,
                    event_type=event.event_type,
                    src_path=str(event.src_path),
                )
                event_log.debug(">>> Processing received event details")

                # Inner Try/Except for event processing logic
                try:
                    # Handle special config file changes
                    if repo_id == "__config__":
                        # Only trigger reload if the actual config file changed
                        if event.src_path.name != self.config_path.name:
                            event_log.debug(
                                "Ignoring non-config file change in config directory",
                                file=event.src_path.name,
                                config_file=self.config_path.name
                            )
                            # Skip processing but don't continue - let finally block handle task_done
                        else:
                            event_log.info("Config file change detected")
                            self._console_message(
                                f"Config file changed: {event.src_path.name}",
                                style="yellow bold",
                                emoji="🔄",
                            )
                            self._post_tui_log(
                                None, "INFO", "🔄 Config file change detected, reloading..."
                            )

                            # Schedule config reload
                            reload_task = asyncio.create_task(
                                self.reload_config(), name=f"ConfigReload-{time.monotonic()}"
                            )
                            self._running_tasks.add(reload_task)
                            reload_task.add_done_callback(self._running_tasks.discard)
                        continue

                    repo_state = self.repo_states.get(repo_id)
                    repo_config = self.config.repositories.get(repo_id) if self.config else None

                    if not repo_state or not repo_config:
                        event_log.warning(
                            "Ignoring event for unknown/disabled/unconfigured repository."
                        )

                    else:
                        # Check for duplicate delete events from move operations
                        if event.event_type == "deleted":
                            # Check if we recently saw a move event for this file
                            move_key = f"{repo_id}:{event.src_path}"
                            current_time = time.time()
                            
                            # Clean up old move events (older than 2 seconds)
                            self._recent_moves = {
                                k: v for k, v in self._recent_moves.items() 
                                if current_time - v < 2.0
                            }
                            
                            if move_key in self._recent_moves:
                                event_log.debug(
                                    "Ignoring delete event that's part of a recent move",
                                    file=event.src_path.name
                                )
                                continue
                        elif event.event_type == "moved":
                            # Record this move to suppress duplicate delete events
                            move_key = f"{repo_id}:{event.src_path}"
                            self._recent_moves[move_key] = time.time()
                        
                        # Record change, check rules, schedule actions/timers
                        repo_state.record_change()  # This calls update_status, which sets emoji for CHANGED
                        event_log = event_log.bind(
                            save_count=repo_state.save_count,
                            status=repo_state.status.name,
                        )
                        # Map event types to human-readable descriptions and emojis
                        event_descriptions = {
                            "created": ("added", "➕"),
                            "modified": ("modified", "✏️"),
                            "deleted": ("deleted", "➖"),
                            "moved": ("moved", "➡️")
                        }
                        
                        event_desc, event_emoji = event_descriptions.get(
                            event.event_type, ("changed", "📝")
                        )
                        
                        # Format message based on event type
                        if event.event_type == "moved" and event.dest_path:
                            display_msg = f"File {event_desc}: {event.src_path.name} → {event.dest_path.name}"
                        else:
                            display_msg = f"File {event_desc}: {event.src_path.name}"
                        
                        self._console_message(
                            display_msg,
                            repo_id=repo_id,
                            style="magenta bold",
                            emoji=event_emoji,
                        )
                        # Also post to TUI log
                        self._post_tui_log(
                            repo_id, 
                            "INFO", 
                            f"{event_emoji} {display_msg}"
                        )
                        
                        # Update file statistics after change
                        # Only update change counts, not total files (to avoid flashing)
                        try:
                            repo_engine = self.repo_engines.get(repo_id)
                            if repo_engine:
                                # Always get current status to check if repository is clean
                                status_result = await repo_engine.get_status(
                                    repo_state, 
                                    repo_config.repository,
                                    self.config.global_config if self.config else {},
                                    repo_config.path
                                )
                                if status_result.success:
                                    # Update all counts
                                    repo_state.changed_files = status_result.changed_files
                                    repo_state.added_files = status_result.added_files
                                    repo_state.deleted_files = status_result.deleted_files
                                    repo_state.modified_files = status_result.modified_files
                                    repo_state.has_uncommitted_changes = not status_result.is_clean
                                    repo_state.current_branch = status_result.current_branch
                                    if repo_state.total_files == 0:
                                        repo_state.total_files = status_result.total_files
                                    
                                    # If repository is now clean (e.g., file added then deleted), skip timer setup
                                    if status_result.is_clean:
                                        event_log.info("Repository is clean after change, no timer needed")
                                        repo_state.update_status(RepositoryStatus.IDLE)
                                        
                                        # Check if there was an active timer that needs to be cancelled
                                        if repo_state.inactivity_timer_handle:
                                            self._post_tui_log(
                                                repo_id,
                                                "INFO",
                                                "⏰ Timer cancelled - repository is clean"
                                            )
                                        else:
                                            self._post_tui_log(
                                                repo_id,
                                                "INFO",
                                                "✅ Repository clean - no changes to commit"
                                            )
                                        
                                        self._post_tui_state_update()
                                        continue  # Skip the rest of the processing
                                    
                                    event_log.debug(
                                        "File statistics updated",
                                        total=repo_state.total_files,
                                        changed=repo_state.changed_files,
                                        is_clean=status_result.is_clean
                                    )
                                else:
                                    # If status check failed, assume there are changes
                                    repo_state.has_uncommitted_changes = True
                                    
                                # Update refresh timestamp
                                self._last_stats_refresh[repo_id] = time.time()
                            else:
                                # No engine available, assume there are changes
                                repo_state.has_uncommitted_changes = True
                        except Exception as e:
                            event_log.debug("Could not update file statistics", error=str(e))
                            # On error, assume there are changes
                            repo_state.has_uncommitted_changes = True
                        # self._post_tui_log(repo_id, "DEBUG", f"Change: {event.event_type} {event.src_path.name}") # Redundant

                        rule_config_obj: RuleConfig = repo_config.rule
                        rule_type_str_lc = getattr(rule_config_obj, "type", "default").lower()
                        repo_state.rule_emoji = RULE_EMOJI_MAP.get(
                            rule_type_str_lc, RULE_EMOJI_MAP["default"]
                        )
                        if isinstance(rule_config_obj, InactivityRuleConfig):
                            repo_state.active_rule_description = (
                                f"Inactivity ({rule_config_obj.period.total_seconds():.0f}s)"
                            )
                            repo_state.rule_dynamic_indicator = (
                                f"({rule_config_obj.period.total_seconds():.0f}s period)"
                            )
                        else:
                            repo_state.active_rule_description = (
                                f"{rule_type_str_lc.capitalize()} pending"
                            )
                            repo_state.rule_dynamic_indicator = (
                                f"{rule_type_str_lc.capitalize()} pending"
                            )
                        self._post_tui_state_update()  # Update TUI with new rule description
                        event_log.debug(
                            "State updated after recording change and setting rule description"
                        )

                        # Rule evaluation phase
                        repo_state.display_status_emoji = "🧪"  # Evaluating emoji
                        repo_state.active_rule_description = (
                            f"Evaluating {rule_type_str_lc.capitalize()}..."
                        )
                        repo_state.rule_dynamic_indicator = (
                            "Checking..."  # Dynamic indicator for evaluation
                        )
                        self._console_message(
                            f"Evaluating {rule_type_str_lc.capitalize()} rule",
                            repo_id=repo_id,
                            style="cyan",
                            emoji="🧪",
                        )
                        self._post_tui_state_update()  # Update TUI for evaluating state
                        event_log.debug(
                            ">>> About to check trigger condition",
                            rule_type=rule_type_str_lc,
                        )

                        rule_met = check_trigger_condition(repo_state, repo_config)
                        event_log.debug("Rule check evaluated", rule_met=rule_met)

                        if rule_met:
                            event_log.info(
                                "Rule condition met, scheduling action",
                                rule_type=rule_type_str_lc,
                            )
                            # Use time.monotonic() for potentially more unique task names
                            action_task = asyncio.create_task(
                                self._trigger_action_callback(repo_id),
                                name=f"Action-{repo_id}-{time.monotonic()}",
                            )
                            self._running_tasks.add(action_task)
                            action_task.add_done_callback(self._running_tasks.discard)
                        else:
                            if isinstance(rule_config_obj, InactivityRuleConfig):
                                # Don't schedule timer if repository is paused
                                if repo_state.is_paused:
                                    event_log.debug("Skipping timer scheduling - repository is paused")
                                    repo_state.display_status_emoji = "⏸️"
                                    repo_state.rule_dynamic_indicator = "Paused"
                                    self._post_tui_state_update()
                                else:
                                    delay = rule_config_obj.period.total_seconds()
                                    event_log.debug(
                                        "Rescheduling inactivity check", delay_seconds=delay
                                    )
                                    # Update state for "Waiting"
                                    repo_state.display_status_emoji = (
                                        "😴"  # Already done by worker (this is a specific state)
                                    )
                                    repo_state.active_rule_description = (
                                        f"Inactivity ({delay:.0f}s waiting)"
                                    )
                                    repo_state.rule_emoji = RULE_EMOJI_MAP.get("inactivity", "⏳")
                                    repo_state.rule_dynamic_indicator = (
                                        f"({int(delay)}s left)"  # Placeholder, real countdown later
                                    )
                                    self._console_message(
                                        f"Waiting for inactivity period ({delay:.0f}s)...",
                                        repo_id=repo_id,
                                        style="italic yellow",
                                        emoji="⏳",
                                    )
                                    # self._post_tui_log(repo_id, "DEBUG", f"Activity detected, rescheduling check in {delay:.1f}s.") # Redundant
                                    self._post_tui_state_update()  # Update TUI for waiting state

                                    current_loop = asyncio.get_running_loop()
                                    # Ensure the callback lambda creates a task
                                    timer_handle = current_loop.call_later(
                                        delay,
                                        lambda rid=repo_id: asyncio.create_task(
                                            self._trigger_action_callback(rid)
                                        ),
                                    )
                                    repo_state.set_inactivity_timer(timer_handle, int(delay))
                            # else: Rule not met, but not inactivity, so emoji/description might reset or stay 'Evaluating'
                            # The next event or state change will update it. Or reset to default rule description.
                            # For now, if not rule_met and not inactivity, it implicitly goes back to its base status emoji on next cycle.

                except Exception as processing_exc:
                    event_log.error(
                        "Error during event processing logic",
                        error=str(processing_exc),
                        exc_info=True,
                    )
                    if repo_id and repo_id in self.repo_states:
                        self.repo_states[repo_id].update_status(
                            RepositoryStatus.ERROR,
                            f"Event processing error: {processing_exc}",
                        )
                        # self._post_tui_log(repo_id, "ERROR", f"Event processing error: {processing_exc}") # Redundant
                        self._post_tui_state_update()

            except asyncio.CancelledError:
                consumer_log.info(
                    "Consumer task processing cancelled. Cleaning up internal tasks..."
                )
                if get_task and not get_task.done():
                    get_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await get_task
                if shutdown_wait_task and not shutdown_wait_task.done():
                    shutdown_wait_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await shutdown_wait_task
                consumer_log.info("Internal tasks cleanup complete for consumer.")
                # It's important to re-raise the CancelledError if this is not the outermost cancellation handler
                # for this task, or if the surrounding architecture expects it.
                # However, given this task is directly managed and cancelled by the orchestrator's
                # finally block, simply cleaning up and exiting the loop (which will happen
                # as the while condition is checked or the error propagates up) is usually sufficient.
                # For now, just log and the loop will terminate or the error will propagate.
            except Exception as e:
                current_repo_id = repo_id if repo_id else "UNKNOWN_PRE_ASSIGNMENT"
                consumer_log.error(
                    "Error in consumer main try block",
                    repo_id=current_repo_id,
                    error=str(e),
                    exc_info=True,
                )

            finally:
                if event is not None:
                    try:
                        self.event_queue.task_done()
                    except ValueError:
                        consumer_log.warning(
                            "queue.task_done() called unexpectedly.",
                            event_processed=bool(event),
                        )
                    except Exception as td_exc:
                        consumer_log.error("Error calling queue.task_done()", error=str(td_exc))

        consumer_log.info(
            f"Event consumer finished after processing {processed_event_count} events."
        )

    async def _initialize_repositories(self) -> list[str]:
        """Initializes states, loads engines, and logs initial repo summary."""
        enabled_repo_ids = []
        if not self.config:
            self._safe_log("error", "Config missing, cannot initialize repos.")
            return []

        self._safe_log("info", "--- Initializing Repositories ---")
        # self._console_message("Initializing repositories...", style="dim", emoji="📂") # Moved to run method
        # self._post_tui_log(None, "INFO", "Initializing repositories...") # Redundant
        for repo_id, repo_config in self.config.repositories.items():
            init_log = self._log.bind(repo_id=repo_id)
            repo_state = None  # Initialize for broader scope

            if repo_config.enabled and repo_config._path_valid:
                init_log.debug("Initializing state object")
                repo_state = RepositoryState(repo_id=repo_id)
                self.repo_states[repo_id] = repo_state
                engine_instance: RepositoryEngine | None = None

                # --- Load Engine ---
                engine_config = repo_config.repository
                engine_type = engine_config.get("type")
                if not engine_type or not isinstance(engine_type, str):
                    init_log.error(
                        "Repo config missing 'type' for engine.",
                        config_section=engine_config,
                    )
                    self.repo_states[repo_id].update_status(
                        RepositoryStatus.ERROR, "Missing engine type"
                    )
                    # self._post_tui_log(repo_id, "ERROR", "Config Error: Missing engine type.") # Redundant
                    continue

                try:
                    init_log.debug("Loading repo engine", engine_type=engine_type)
                    # --- TODO: Replace with plugin loading logic ---
                    if engine_type == "supsrc.engines.git":
                        engine_instance = GitEngine()
                    # elif engine_type == "plugin:my_custom_hg_engine": engine_instance = load_plugin(...)
                    else:
                        raise NotImplementedError(f"Engine type '{engine_type}' not supported.")
                    # ---------------------------------------------
                    self.repo_engines[repo_id] = engine_instance
                    init_log.debug("Engine loaded ok.", engine_class=type(engine_instance).__name__)
                    # self._post_tui_log(repo_id, "DEBUG", f"Engine '{engine_type}' loaded.") # Redundant
                    enabled_repo_ids.append(repo_id)
                except Exception as load_exc:
                    init_log.error(
                        "Failed to load repo engine",
                        engine_type=engine_type,
                        error=str(load_exc),
                        exc_info=True,
                    )
                    if repo_state:
                        repo_state.update_status(
                            RepositoryStatus.ERROR, f"Failed to load engine: {load_exc}"
                        )
                    # self._post_tui_log(repo_id, "ERROR", f"Engine load failed: {load_exc}") # Redundant
                    continue

                # --- Set initial rule description, emoji, and dynamic indicator ---
                if repo_state:
                    rule_conf_obj = repo_config.rule
                    rule_type_str = getattr(rule_conf_obj, "type", "default").lower()
                    repo_state.rule_emoji = RULE_EMOJI_MAP.get(
                        rule_type_str, RULE_EMOJI_MAP["default"]
                    )

                    if isinstance(rule_conf_obj, InactivityRuleConfig):
                        repo_state.active_rule_description = (
                            f"Inactivity ({rule_conf_obj.period.total_seconds():.0f}s)"  # Existing
                        )
                        repo_state.rule_dynamic_indicator = (
                            f"({rule_conf_obj.period.total_seconds():.0f}s period)"
                        )
                    elif hasattr(rule_conf_obj, "type"):
                        repo_state.active_rule_description = (
                            f"{getattr(rule_conf_obj, 'type', 'Unknown')} rule"  # Existing
                        )
                        repo_state.rule_dynamic_indicator = rule_type_str.capitalize()
                    else:
                        repo_state.active_rule_description = "Default rule"  # Existing
                        repo_state.rule_dynamic_indicator = "Default"

                # --- Get Initial Summary ---
                if repo_state and engine_instance:  # Ensure repo_state and engine are valid
                    try:
                        init_log.debug("Getting initial repo summary...")
                        if hasattr(engine_instance, "get_summary"):
                            summary = cast(
                                GitRepoSummary,
                                await engine_instance.get_summary(repo_config.path),
                            )

                            if summary.head_ref_name == "ERROR":
                                summary_msg = f"Init Error: {summary.head_commit_message_summary}"
                                init_log.error(summary_msg)
                                # self._post_tui_log(repo_id, "ERROR", summary_msg) # Redundant
                                # repo_state.update_status(RepositoryStatus.ERROR, summary_msg) # Already handled by engine?
                            elif summary.is_empty:
                                summary_msg = "Init: Repository empty."
                                init_log.info(summary_msg)
                                # self._post_tui_log(repo_id, "INFO", summary_msg) # Redundant
                            elif summary.head_ref_name == "UNBORN":
                                summary_msg = "Init: Repository has no commits (unborn HEAD)"
                                init_log.info(summary_msg)
                                # self._post_tui_log(repo_id, "INFO", summary_msg) # Redundant
                            elif not summary.head_ref_name or not summary.head_commit_hash:
                                summary_msg = "Init: Unable to determine HEAD reference or commit."
                                init_log.warning(summary_msg)
                                # self._post_tui_log(repo_id, "WARNING", summary_msg) # Redundant
                            else:
                                repo_state.last_commit_short_hash = (
                                    summary.head_commit_hash[:7]
                                    if summary.head_commit_hash
                                    else None
                                )
                                repo_state.last_commit_message_summary = (
                                    summary.head_commit_message_summary
                                )
                                # Set the actual Git commit timestamp
                                repo_state.last_commit_timestamp = summary.head_commit_timestamp
                                commit_short_hash = repo_state.last_commit_short_hash or "N/A"
                                commit_msg_summary = (
                                    repo_state.last_commit_message_summary or "No commit message"
                                )
                                summary_msg = f"Init: HEAD at {summary.head_ref_name} ({commit_short_hash}) | {commit_msg_summary}"
                                init_log.info(summary_msg)
                                # self._post_tui_log(repo_id, "INFO", summary_msg) # Redundant
                                self._console_message(
                                    f"Watching: {repo_config.path} (Branch: {summary.head_ref_name}, Last Commit: {commit_short_hash})",
                                    repo_id=repo_id,
                                    style="dim",
                                    emoji="📂",
                                )
                        else:
                            init_log.warning("Engine lacks get_summary method.")
                            # self._post_tui_log(repo_id, "WARNING", "Engine lacks get_summary.") # Redundant

                    except Exception as summary_exc:
                        init_log.error(
                            "Failed to get initial repo summary",
                            error=str(summary_exc),
                            exc_info=True,
                        )
                        # self._post_tui_log(repo_id, "ERROR", f"Failed to get summary: {summary_exc}") # Redundant
                        # repo_state.update_status(RepositoryStatus.ERROR, f"Summary failed: {summary_exc}") # Potentially set error

                # Check initial repository status (clean vs uncommitted changes)
                if repo_state and engine_instance:
                    try:
                        init_status = await engine_instance.get_status(
                            repo_state, 
                            repo_config.repository, 
                            self.config.global_config if self.config else {}, 
                            repo_config.path
                        )
                        if init_status.success:
                            # Update file statistics from initial status
                            repo_state.total_files = init_status.total_files
                            repo_state.changed_files = init_status.changed_files
                            repo_state.added_files = init_status.added_files
                            repo_state.deleted_files = init_status.deleted_files
                            repo_state.modified_files = init_status.modified_files
                            repo_state.has_uncommitted_changes = not init_status.is_clean
                            repo_state.current_branch = init_status.current_branch
                            
                            init_log.info(
                                f"Initial status for {repo_id}: {repo_state.total_files} files, branch={repo_state.current_branch}, clean={init_status.is_clean}"
                            )
                            
                            # Mark initial refresh time
                            self._last_stats_refresh[repo_id] = time.time()
                            
                            if init_status.has_unstaged_changes or init_status.has_staged_changes:
                                repo_state.update_status(RepositoryStatus.CHANGED)
                                # Set initial last_change_time to now for repos with changes
                                from datetime import datetime, UTC
                                repo_state.last_change_time = datetime.now(UTC)
                                init_log.info(f"Repository has uncommitted changes (branch: {repo_state.current_branch}, files: {repo_state.total_files})")
                            else:
                                repo_state.update_status(RepositoryStatus.IDLE)
                                init_log.info(f"Repository is clean (branch: {repo_state.current_branch}, files: {repo_state.total_files})")
                        else:
                            init_log.warning(f"Failed to get initial status: {init_status.message}")
                            repo_state.update_status(RepositoryStatus.IDLE)
                    except Exception as e:
                        init_log.warning(f"Error checking initial status: {e}")
                        # Still set to IDLE if we can't determine status
                        repo_state.update_status(RepositoryStatus.IDLE)
                    
            else:  # Not enabled or path not valid
                init_log.info(
                    "Skipping initialization (disabled or invalid path)",
                    enabled=repo_config.enabled,
                    path_valid=repo_config._path_valid,
                )
                # If you want to show disabled repos in TUI, you could add a placeholder state here
                # e.g., self.repo_states[repo_id] = RepositoryState(repo_id=repo_id, status=RepositoryStatus.DISABLED_OR_INVALID_TYPE_MAYBE?)
                # For now, they are just skipped.

        self._log.debug(
            "Orchestrator: Final repo_states before initial TUI update",
            num_states=len(self.repo_states),
            repo_ids=list(self.repo_states.keys()),
        )
        
        # Log final state for debugging
        for repo_id, repo_state in self.repo_states.items():
            self._log.debug(
                f"Final state for {repo_id}",
                total_files=repo_state.total_files,
                status=repo_state.status.name,
                has_changes=repo_state.has_uncommitted_changes
            )
        
        self._post_tui_state_update()
        self._safe_log("info", "--- Repo Initialization Complete ---", count=len(enabled_repo_ids))
        # self._post_tui_log(None, "INFO", f"{len(enabled_repo_ids)} repos initialized.") # Redundant

        if enabled_repo_ids:
            self._console_message(
                f"Monitoring active for {len(enabled_repo_ids)} repositories.",
                style="dim",
                emoji="✅",
            )
            self._console_message(
                "All repositories idle. Awaiting changes... (Press Ctrl+C to exit)",
                style="dim",
                emoji="🧼",
            )
        return enabled_repo_ids

    def _setup_monitoring(self, enabled_repo_ids: list[str]) -> list[str]:
        """Adds successfully initialized repositories to the MonitoringService."""
        setup_errors = 0
        successfully_added_ids = []
        if not self.config:
            return []

        self._safe_log("info", "Setting up filesystem monitoring...")
        # self._post_tui_log(None, "INFO", "Setting up filesystem monitoring...") # Redundant
        self.monitor_service = MonitoringService(self.event_queue)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._safe_log("critical", "Cannot get running event loop during monitoring setup.")
            return []

        for repo_id in enabled_repo_ids:
            if repo_id not in self.repo_states or repo_id not in self.repo_engines:
                self._safe_log(
                    "warning",
                    "Skipping monitor setup for repo with failed init/engine load",
                    repo_id=repo_id,
                )
                continue

            repo_config = self.config.repositories[repo_id]
            monitor_log = self._log.bind(repo_id=repo_id)
            try:
                monitor_log.debug(
                    "Adding repository to monitoring service",
                    path=str(repo_config.path),
                )
                # Pass the loop to add_repository
                self.monitor_service.add_repository(repo_id, repo_config, loop)
                successfully_added_ids.append(repo_id)
                monitor_log.info("Monitoring successfully scheduled.")
                # self._post_tui_log(repo_id, "INFO", "Monitoring started.") # Redundant
            except MonitoringSetupError as e:
                monitor_log.error("Failed to setup monitoring", error=str(e))
                if repo_id in self.repo_states:
                    self.repo_states[repo_id].update_status(
                        RepositoryStatus.ERROR, f"Monitoring setup failed: {e}"
                    )
                # self._post_tui_log(repo_id, "ERROR", f"Monitoring setup failed: {e}") # Redundant
                self._post_tui_state_update()
                setup_errors += 1
            except Exception as e:
                monitor_log.error(
                    "Unexpected error adding repository to monitor",
                    error=str(e),
                    exc_info=True,
                )
                if repo_id in self.repo_states:
                    self.repo_states[repo_id].update_status(
                        RepositoryStatus.ERROR, f"Unexpected setup error: {e}"
                    )
                # self._post_tui_log(repo_id, "CRITICAL", f"Unexpected monitor setup error: {e}") # Redundant
                self._post_tui_state_update()
                setup_errors += 1

        if setup_errors > 0:
            self._safe_log(
                "warning",
                f"Encountered {setup_errors} error(s) during monitoring setup.",
            )
            # self._post_tui_log(None, "WARNING", f"{setup_errors} monitoring setup error(s).") # Redundant

        self._safe_log(
            "info",
            f"Monitoring setup complete for {len(successfully_added_ids)} repositories.",
        )
        return successfully_added_ids

    async def run(self) -> None:
        """Main execution method for the watch process."""
        self._safe_log(
            "info",
            "Starting orchestrator run",
            config_path=str(self.config_path),
            tui_mode=self._is_tui_active,
        )

        try:
            # Load Config
            self._safe_log("debug", "Loading configuration...")
            try:
                self.config = load_config(self.config_path)
                self._safe_log("info", "Config loaded successfully.")
                self._console_message("Config loaded successfully.", style="dim", emoji="📂")
                # self._post_tui_log(None, "INFO", f"Config loaded: {self.config_path.name}") # Redundant
            except (ConfigurationError, cattrs.BaseValidationError) as e:
                self._safe_log(
                    "error",
                    "Failed to load/validate config",
                    error=str(e),
                    path=str(self.config_path),
                )
                self._console_message(f"Config Error: {e}", style="bold red", emoji="❌")
                # self._post_tui_log(None, "CRITICAL", f"Config Error: {e}") # Redundant
                raise
            except Exception as e:
                self._safe_log(
                    "critical",
                    "Unexpected error loading config",
                    error=str(e),
                    exc_info=True,
                )
                self._console_message(f"Unexpected Config Error: {e}", style="bold red", emoji="❌")
                # self._post_tui_log(None, "CRITICAL", f"Unexpected Config Error: {e}") # Redundant
                raise

            # Initialize Repos
            self._console_message("Initializing repositories...", style="dim", emoji="📂")
            enabled_repo_ids = await self._initialize_repositories()
            if not enabled_repo_ids:
                self._safe_log("warning", "No enabled/valid repos found. Exiting.")
                # self._post_tui_log(None, "WARNING", "No valid/enabled repositories found.") # Redundant
                return

            # Setup & Start Monitoring
            successfully_added_ids = self._setup_monitoring(enabled_repo_ids)
            if not successfully_added_ids:
                self._safe_log("critical", "No repos could be monitored. Exiting.")
                # self._post_tui_log(None, "CRITICAL", "Failed to start monitoring any repository.") # Redundant
                return

            if self.monitor_service:
                self._safe_log("debug", "Starting monitor service thread...")
                self.monitor_service.start()
                if not self.monitor_service.is_running:
                    self._safe_log("critical", "Monitor service failed to start. Exiting.")
                    # self._post_tui_log(None, "CRITICAL", "Monitor service failed to start.") # Redundant
                    return
                # self._post_tui_log(None, "INFO", f"Monitoring active for {len(successfully_added_ids)} repositories.") # Redundant with other logs
            else:
                self._safe_log("error", "Monitor service not initialized after setup.")
                # self._post_tui_log(None, "CRITICAL", "Internal Error: Monitor service missing.") # Redundant
                return

            # Setup config watcher for hot reload
            self.setup_config_watcher()

            # Send initial state update to TUI
            self._post_tui_state_update()

            # Start Consumer Task
            self._safe_log("debug", "Creating event consumer task...")
            consumer_task = asyncio.create_task(self._consume_events(), name="EventConsumer")
            self._running_tasks.add(consumer_task)
            consumer_task.add_done_callback(self._running_tasks.discard)

            self._safe_log("info", "Orchestrator running. Waiting for shutdown signal.")

            # Wait for Shutdown
            await self.shutdown_event.wait()
            self._safe_log("info", "Shutdown signal received.")
            self._console_message(
                "Shutdown requested...", style="dim"
            )  # emoji="INFO" removed to match UX
            # self._post_tui_log(None, "INFO", "Shutdown requested...") # Redundant

        except SupsrcError as e:
            self._safe_log(
                "critical",
                "Critical supsrc error during watch",
                error=str(e),
                exc_info=True,
            )
            self._console_message(f"Runtime Error: {e}", style="bold red", emoji="❌")
            # self._post_tui_log(None, "CRITICAL", f"Runtime Error: {e}") # Redundant
        except asyncio.CancelledError:
            self._safe_log("warning", "Orchestrator run task cancelled.")
            if not self.shutdown_event.is_set():
                self.shutdown_event.set()
        except Exception as e:
            self._safe_log(
                "critical",
                "Unexpected error in orchestrator run",
                error=str(e),
                exc_info=True,
            )
            self._console_message(f"Unexpected Error: {e}", style="bold red", emoji="❌")
            # self._post_tui_log(None, "CRITICAL", f"Unexpected Error: {e}") # Redundant
            if not self.shutdown_event.is_set():
                self.shutdown_event.set()
        finally:
            # Orchestrator Cleanup
            self._safe_log("info", "Orchestrator starting cleanup...")
            self._console_message("Cleaning up...", style="dim")  # emoji="INFO" removed
            # self._post_tui_log(None, "INFO", "Cleaning up...") # Redundant

            # 1. Cancel Repo Timers
            self._safe_log("debug", "Cancelling active repo timers...")
            timers_cancelled = sum(
                1 for state in self.repo_states.values() if state.inactivity_timer_handle
            )
            for state in self.repo_states.values():
                state.cancel_inactivity_timer()
            self._safe_log("debug", f"Cancelled {timers_cancelled} repo timer(s).")

            # 2. Cancel Running Tasks (Consumer)
            tasks_to_cancel = list(self._running_tasks)
            if tasks_to_cancel:
                self._safe_log(
                    "debug",
                    f"Cancelling {len(tasks_to_cancel)} running task(s)...",
                    tasks=[t.get_name() for t in tasks_to_cancel],
                )
                for task in tasks_to_cancel:
                    task.cancel()
                await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
                self._safe_log("debug", "Running tasks cancellation gathered.")
            else:
                self._safe_log("debug", "No running tasks to cancel.")

            # 3. Stop Monitoring Service
            if self.monitor_service and self.monitor_service.is_running:
                self._safe_log("debug", "Stopping monitoring service...")
                try:
                    await self.monitor_service.stop()
                    self._safe_log("debug", "Monitoring service stop completed.")
                    self._console_message(
                        "Monitoring stopped.", style="dim"
                    )  # emoji="INFO" removed
                    # self._post_tui_log(None, "INFO", "Monitoring stopped.") # Redundant
                except Exception as stop_exc:
                    self._safe_log(
                        "error",
                        "Error during monitor service stop",
                        error=str(stop_exc),
                        exc_info=True,
                    )
                    # self._post_tui_log(None, "ERROR", "Error stopping monitor service.") # Redundant
            elif self.monitor_service:
                self._safe_log("debug", "Monitor service already stopped.")
            else:
                self._safe_log("debug", "Monitor service was not initialized.")

            self._safe_log("info", "Orchestrator finished cleanup.")
            self._console_message("Cleanup complete.", style="dim")
            # self._post_tui_log(None, "INFO", "Cleanup complete.") # Redundant

    def _safe_log(self, level: str, msg: str, **kwargs):
        """Helper to suppress logging errors during final shutdown."""
        kwargs["orchestrator_id"] = id(self)
        try:
            if hasattr(self, "_log") and self._log:
                getattr(self._log, level)(msg, **kwargs)
            else:
                pass
        except Exception:
            pass

    async def get_repository_details(self, repo_id: str) -> dict[str, Any]:
        """
        Retrieves detailed information for a given repository,
        currently focused on commit history.
        """
        details_log = self._log.bind(repo_id=repo_id)
        details_log.debug("Fetching repository details for TUI")

        repo_state = self.repo_states.get(repo_id)
        repo_config = self.config.repositories.get(repo_id) if self.config else None
        repo_engine = self.repo_engines.get(repo_id)

        if not repo_state or not repo_config or not repo_engine:
            details_log.warning("Could not find state, config, or engine for repo details.")
            return {"error": "Repository data not found."}

        commit_history = []
        if isinstance(repo_engine, GitEngine):  # Check if it's the GitEngine
            try:
                # Ensure repo_config.path is a Path object if your engine expects it
                # The get_commit_history is synchronous, run it in a thread pool
                # to avoid blocking the orchestrator's async loop if it were very slow,
                # though for local git log, it's often fast enough.
                # For simplicity in this subtask, we'll call it directly if it's reasonably fast.
                # If performance issues arise, this should be wrapped with loop.run_in_executor.

                # The config stores path as str, GitEngine might need Path
                history = repo_engine.get_commit_history(Path(repo_config.path), limit=15)
                commit_history.extend(history)
            except Exception as e:
                details_log.error("Error fetching commit history from GitEngine", error=str(e))
                commit_history.append(f"Error fetching history: {e}")
        else:
            commit_history.append("Detail view not supported for this engine type.")
            details_log.warning(
                "Detail view requested for non-Git engine type",
                engine_type=type(repo_engine).__name__,
            )

        return {
            "repo_id": repo_id,
            "commit_history": commit_history,
            # Add other details here in the future if needed
        }

    # --- Pause/Suspend Control Methods ---

    def pause_monitoring(self) -> None:
        """Pause monitoring - events are queued but not processed."""
        self._is_paused = True
        self._log.info("Global monitoring PAUSED")
        for repo_state in self.repo_states.values():
            repo_state.is_paused = True
        self._post_tui_state_update()

    def suspend_monitoring(self) -> None:
        """Suspend monitoring - stronger than pause, stops file watching."""
        self._is_suspended = True
        self._is_paused = True  # Suspend implies pause
        self._log.info("Global monitoring SUSPENDED")
        for repo_state in self.repo_states.values():
            repo_state.is_stopped = True # Set individual repo to stopped
        if self.monitor_service:
            # Actually stop the watchers
            self.monitor_service.stop()
        self._post_tui_state_update()

    def resume_monitoring(self) -> None:
        """Resume monitoring from pause or suspend state."""
        was_suspended = self._is_suspended
        self._is_paused = False
        self._is_suspended = False

        if was_suspended:
            self._log.info("Global monitoring RESUMED from suspension")
            # Restart the monitor service if it was suspended
            if self.monitor_service and self.config:
                self._log.info("Restarting monitor service...")
                asyncio.create_task(self._restart_monitor_service())
            for repo_state in self.repo_states.values():
                repo_state.is_stopped = False # Unstop individual repos
        else:
            self._log.info("Global monitoring RESUMED from pause")
            for repo_state in self.repo_states.values():
                repo_state.is_paused = False # Unpause individual repos
        self._post_tui_state_update()
    
    def toggle_repository_pause(self, repo_id: str) -> bool:
        """Toggle pause state for an individual repository."""
        repo_state = self.repo_states.get(repo_id)
        if not repo_state:
            return False
            
        repo_state.is_paused = not repo_state.is_paused
        
        if repo_state.is_paused:
            repo_state.pause_until = datetime.now(UTC) + timedelta(hours=1)  # Default 1 hour pause
            repo_state.display_status_emoji = "⏸️"
            self._log.info(f"Repository {repo_id} PAUSED")
            self._console_message(
                f"Repository {repo_id} paused for 1 hour",
                repo_id=repo_id,
                style="yellow bold",
                emoji="⏸️",
            )
        else:
            repo_state.pause_until = None
            # Restore normal emoji based on status
            repo_state.display_status_emoji = STATUS_EMOJI_MAP.get(repo_state.status, "❓")
            self._log.info(f"Repository {repo_id} RESUMED")
            self._console_message(
                f"Repository {repo_id} resumed",
                repo_id=repo_id,
                style="green bold",
                emoji="▶️",
            )
            
        self._post_tui_state_update()
        return True
    
    def toggle_repository_pause(self, repo_id: str) -> bool:
        """Toggle pause state for an individual repository."""
        repo_state = self.repo_states.get(repo_id)
        if not repo_state:
            return False
            
        repo_state.is_paused = not repo_state.is_paused
        
        if repo_state.is_paused:
            repo_state.pause_until = datetime.now(UTC) + timedelta(hours=1)  # Default 1 hour pause
            
            # Cancel any existing timer when pausing
            if repo_state.inactivity_timer_handle:
                self._log.debug(f"Canceling inactivity timer for paused repository {repo_id}")
                repo_state.cancel_inactivity_timer()
            
            # Force emoji update
            old_emoji = repo_state.display_status_emoji
            repo_state._update_display_emoji()  # Update the emoji
            new_emoji = repo_state.display_status_emoji
            
            self._log.info(f"Repository {repo_id} PAUSED - emoji: '{old_emoji}' -> '{new_emoji}'")
            self._console_message(
                f"Repository {repo_id} paused for 1 hour",
                repo_id=repo_id,
                style="yellow bold",
                emoji="⏸️",
            )
            self._post_tui_log(
                repo_id,
                "WARNING",
                "⏸️ Repository paused - commits disabled for 1 hour, timer stopped"
            )
        else:
            repo_state.pause_until = None
            repo_state._update_display_emoji()  # Update the emoji
            self._log.info(f"Repository {repo_id} RESUMED")
            self._console_message(
                f"Repository {repo_id} resumed",
                repo_id=repo_id,
                style="green bold",
                emoji="▶️",
            )
            
            # If there are uncommitted changes, restart the inactivity timer
            if repo_state.has_uncommitted_changes and self.config:
                repo_config = self.config.repositories.get(repo_id)
                if repo_config and isinstance(repo_config.rule, InactivityRuleConfig):
                    delay = repo_config.rule.period.total_seconds()
                    self._log.debug(f"Restarting inactivity timer for {repo_id} with {delay}s delay")
                    
                    # Schedule the timer
                    current_loop = asyncio.get_running_loop()
                    timer_handle = current_loop.call_later(
                        delay,
                        lambda rid=repo_id: asyncio.create_task(
                            self._trigger_action_callback(rid)
                        ),
                    )
                    repo_state.set_inactivity_timer(timer_handle, int(delay))
                    repo_state.display_status_emoji = "😴"
                    repo_state.rule_dynamic_indicator = f"({int(delay)}s left)"
                    
                    self._post_tui_log(
                        repo_id,
                        "INFO",
                        f"▶️ Repository resumed - timer restarted ({int(delay)}s)"
                    )
                else:
                    self._post_tui_log(
                        repo_id,
                        "INFO",
                        "▶️ Repository resumed - commits enabled"
                    )
            else:
                self._post_tui_log(
                    repo_id,
                    "INFO",
                    "▶️ Repository resumed - commits enabled"
                )
            
        self._post_tui_state_update()
        return True
    
    async def toggle_repository_stop(self, repo_id: str) -> bool:
        """Toggle stop state for an individual repository (stops monitoring)."""
        repo_state = self.repo_states.get(repo_id)
        if not repo_state:
            return False

        repo_state.is_stopped = not repo_state.is_stopped

        if repo_state.is_stopped:
            repo_state._update_display_emoji()  # Update the emoji
            self._log.info(f"Repository {repo_id} STOPPED from monitoring.")
            self._console_message(
                f"Repository {repo_id} stopped from monitoring",
                repo_id=repo_id,
                style="red bold",
                emoji="⏹️",
            )
            # Unschedule from watchdog observer
            if self.monitor_service:
                try:
                    self.monitor_service.unschedule_repository(repo_id)
                    self._log.debug(f"Unscheduled {repo_id} from watchdog.")
                except Exception as e:
                    self._log.error(f"Failed to unschedule {repo_id} from watchdog: {e}")
                    return False
        else:
            repo_state._update_display_emoji()  # Update the emoji
            self._log.info(f"Repository {repo_id} RESUMED monitoring.")
            self._console_message(
                f"Repository {repo_id} resumed monitoring",
                repo_id=repo_id,
                style="green bold",
                emoji="▶️",
            )
            # Re-schedule with watchdog observer
            if self.monitor_service and self.config:
                repo_config = self.config.repositories.get(repo_id)
                if repo_config:
                    try:
                        loop = asyncio.get_running_loop()
                        self.monitor_service.add_repository(repo_id, repo_config, loop)
                        self._log.debug(f"Rescheduled {repo_id} with watchdog.")
                    except Exception as e:
                        self._log.error(f"Failed to reschedule {repo_id} with watchdog: {e}")
                        return False
                else:
                    self._log.warning(f"Cannot resume monitoring for {repo_id}: config not found.")
                    return False

        self._post_tui_state_update()
        return True

    async def refresh_repository_status(self, repo_id: str) -> bool:
        """Force a refresh of the repository's status."""
        repo_state = self.repo_states.get(repo_id)
        repo_config = self.config.repositories.get(repo_id) if self.config else None
        repo_engine = self.repo_engines.get(repo_id)

        if not repo_state or not repo_config or not repo_engine:
            self._log.warning(f"Cannot refresh status for {repo_id}: state/config/engine missing.")
            return False

        self._log.info(f"Refreshing status for {repo_id}...")
        try:
            # This will update the repo_state with latest file counts and status
            status_result = await repo_engine.get_status(
                repo_state,
                repo_config.repository,
                self.config.global_config if self.config else {},
                repo_config.path
            )
            if status_result.success:
                # Update all relevant fields in repo_state from status_result
                repo_state.total_files = status_result.total_files
                repo_state.changed_files = status_result.changed_files
                repo_state.added_files = status_result.added_files
                repo_state.deleted_files = status_result.deleted_files
                repo_state.modified_files = status_result.modified_files
                repo_state.has_uncommitted_changes = not status_result.is_clean
                repo_state.current_branch = status_result.current_branch
                
                # Update last refresh time
                self._last_stats_refresh[repo_id] = time.time()
                self._post_tui_state_update()
                self._log.info(f"Status for {repo_id} refreshed successfully.")
                return True
            else:
                self._log.error(f"Failed to get status for {repo_id}: {status_result.message}")
                return False
        except Exception as e:
            self._log.error(f"Error refreshing status for {repo_id}: {e}", exc_info=True)
            return False

    async def resume_repository_monitoring(self, repo_id: str) -> bool:
        """Resumes monitoring for a repository (unpauses and unstops it)."""
        repo_state = self.repo_states.get(repo_id)
        if not repo_state:
            return False

        # Unpause if paused
        if repo_state.is_paused:
            repo_state.is_paused = False
            repo_state.pause_until = None
            self._log.info(f"Repository {repo_id} unpaused.")

        # Unstop if stopped
        if repo_state.is_stopped:
            success = await self.toggle_repository_stop(repo_id) # This will re-schedule
            if not success:
                self._log.error(f"Failed to unstop {repo_id} during resume.")
                return False
            self._log.info(f"Repository {repo_id} unstopped.")
        
        self._post_tui_state_update()
        return True

    def set_repo_refreshing_status(self, repo_id: str, is_refreshing: bool) -> None:
        """Sets the refreshing status for a repository."""
        repo_state = self.repo_states.get(repo_id)
        if repo_state:
            repo_state.is_refreshing = is_refreshing
            self._post_tui_state_update()

    def unfreeze_repository(self, repo_id: str) -> bool:
        """Unfreeze a frozen repository."""
        repo_state = self.repo_states.get(repo_id)
        if not repo_state or not repo_state.is_frozen:
            return False
            
        repo_state.is_frozen = False
        repo_state.freeze_reason = None
        repo_state.display_status_emoji = STATUS_EMOJI_MAP.get(repo_state.status, "❓")
        
        if repo_state.status == RepositoryStatus.ERROR:
            repo_state.update_status(RepositoryStatus.IDLE)
            
        self._log.info(f"Repository {repo_id} UNFROZEN")
        self._console_message(
            f"Repository {repo_id} unfrozen",
            repo_id=repo_id,
            style="green bold",
            emoji="✅",
        )
        
        self._post_tui_state_update()
        return True

    async def _restart_monitor_service(self) -> None:
        """Restart the monitor service after suspension."""
        if not self.config:
            return

        # Start fresh monitor service
        self.monitor_service = MonitoringService(self.event_queue, self.config)

        # Restart monitoring for each repository
        for repo_id, repo_config in self.config.repositories.items():
            if repo_config.enabled:
                await self.monitor_service.start_monitoring(repo_id, repo_config)

    async def reload_config(self) -> bool:
        """
        Reload configuration from disk.
        Temporarily pauses monitoring (90s) to allow safe reload.
        Returns True if successful, False otherwise.
        """
        reload_log = self._log.bind(action="reload_config")
        reload_log.info("Config reload requested")

        # Pause monitoring during reload
        original_pause_state = self._is_paused
        self.pause_monitoring()
        self._console_message(
            "⏸️  Pausing monitoring for config reload...", style="yellow", emoji="⚠️"
        )
        self._post_tui_log(None, "WARNING", "⏸️  Config reload in progress...")

        try:
            # Load new config
            reload_log.debug("Loading new configuration from disk", path=str(self.config_path))
            new_config = load_config(self.config_path)

            # Validate new config has at least one enabled repository
            enabled_repos = [r for r, cfg in new_config.repositories.items() if cfg.enabled]
            if not enabled_repos:
                raise ConfigurationError("New config has no enabled repositories")

            # Store old config for rollback
            old_config = self.config
            old_monitor_service = self.monitor_service

            # Apply new config
            self.config = new_config
            reload_log.info("New config loaded successfully", enabled_repos=len(enabled_repos))

            # Stop current monitoring (if running)
            if self.monitor_service and self.monitor_service.is_running:
                await self.monitor_service.stop() # Ensure it's fully stopped
                self.monitor_service.clear_handlers() # Clear handlers from the old observer

            # Re-initialize repositories with new config
            reload_log.debug("Re-initializing repositories")
            enabled_repo_ids = await self._initialize_repositories()

            # Setup new monitoring with new config (reuse existing monitor_service)
            reload_log.debug("Setting up monitoring with new config")
            # Ensure monitor_service exists, if not, create it (should exist from run())
            if not self.monitor_service:
                self.monitor_service = MonitoringService(self.event_queue)

            successfully_added_ids = self._setup_monitoring(enabled_repo_ids)

            if not successfully_added_ids:
                raise MonitoringSetupError("Failed to setup monitoring with new config")

            # Start new monitor service (if not already running)
            if self.monitor_service and not self.monitor_service.is_running:
                self.monitor_service.start()
                reload_log.info("Monitor service restarted with new config")

            # Update TUI
            self._post_tui_state_update()

            # Success message
            self._console_message(
                f"✅ Config reloaded successfully ({len(successfully_added_ids)} repositories)",
                style="green bold",
                emoji="🔄",
            )
            self._post_tui_log(
                None,
                "SUCCESS",
                f"✅ Config reloaded: monitoring {len(successfully_added_ids)} repositories",
            )

            # If successful, resume monitoring if it wasn't paused before
            if not original_pause_state:
                self.resume_monitoring()

            return True

        except Exception as e:
            # Rollback on error
            reload_log.error("Config reload failed, rolling back", error=str(e))
            if 'old_config' in locals():
                self.config = old_config
            if 'old_monitor_service' in locals():
                self.monitor_service = old_monitor_service

            self._console_message(f"❌ Config reload failed: {e}", style="red bold", emoji="⚠️")
            self._post_tui_log(None, "ERROR", f"❌ Config reload failed: {e}")

            # Resume monitoring if it wasn't paused before
            if not original_pause_state:
                self.resume_monitoring()

            return False

    def setup_config_watcher(self) -> None:
        """
        Setup a file watcher for the config file to enable hot reload.
        """
        if not self.monitor_service or not self.config:
            return

        watcher_log = self._log.bind(action="setup_config_watcher")
        watcher_log.info("Setting up config file watcher", path=str(self.config_path))

        # Create a special repository config for watching the config file
        from supsrc.config.models import ManualRuleConfig, RepositoryConfig

        config_repo = RepositoryConfig(
            enabled=True,
            path=self.config_path.parent,
            rule=ManualRuleConfig(),  # Use manual rule for config file
            repository={},  # Empty repository config
        )

        # Add config watcher as a special monitored path
        try:
            self.monitor_service.add_repository("__config__", config_repo, asyncio.get_running_loop())
            watcher_log.info("Config file watcher setup complete")
        except Exception as e:
            watcher_log.error("Failed to setup config watcher", error=str(e))


# 🔼⚙️

#
# supsrc/tui/app.py
#
"""
Stabilized TUI application with improved layout and proper timer management.
"""

import asyncio
from pathlib import Path
import sys # Ensure this import is present
from typing import Any

import structlog
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.reactive import var
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Header
from textual.widgets import Log as TextualLog
from textual.worker import Worker

from supsrc.runtime.orchestrator import RepositoryStatesMap, WatchOrchestrator
from supsrc.state import RepositoryState, RepositoryStatus # Added import

log = structlog.get_logger("tui.app")

# Custom Messages
class StateUpdate(Message):
    ALLOW_BUBBLE = True
    def __init__(self, repo_states: RepositoryStatesMap) -> None:
        self.repo_states = repo_states
        super().__init__()

class LogMessageUpdate(Message):
    ALLOW_BUBBLE = True
    def __init__(self, repo_id: str | None, level: str, message: str) -> None:
        self.repo_id = repo_id
        self.level = level
        self.message = message
        super().__init__()

class RepoDetailUpdate(Message):
    ALLOW_BUBBLE = True
    def __init__(self, repo_id: str, details: dict[str, Any]) -> None:
        self.repo_id = repo_id
        self.details = details
        super().__init__()


class TimerManager:
    """Manages application timers with proper lifecycle handling."""

    def __init__(self, app: "SupsrcTuiApp") -> None:
        self.app = app
        self._timers: dict[str, Timer] = {}
        self._logger = log.bind(component="TimerManager")

    def create_timer(
        self,
        name: str,
        interval: float,
        callback: callable,
        repeat: bool = True
    ) -> Timer:
        """Create a new timer with proper tracking."""
        if name in self._timers:
            self.stop_timer(name)

        timer = self.app.set_interval(interval, callback, name=name)
        self._timers[name] = timer
        self._logger.debug("Timer created", name=name, interval=interval)
        return timer

    def stop_timer(self, name: str) -> bool:
        """Stop a specific timer."""
        if name not in self._timers:
            return False

        timer = self._timers[name]
        try:
            # Check if the timer is active by inspecting its internal handle
            if hasattr(timer, '_Timer__handle') and timer._Timer__handle is not None:
                timer.stop()
            # No need to check is_cancelled, stop() should be idempotent or handle internal state.
            # Textual's stop() method on Timer sets _Timer__handle to None.
            if name in self._timers: # Re-check as timer.stop() might have already removed it via a callback
                del self._timers[name]
            self._logger.debug("Timer stopped or already inactive", name=name)
            return True
        except Exception as e:
            self._logger.error("Error stopping timer", name=name, error=str(e))
            return False

    def stop_all_timers(self) -> None:
        """Stop all managed timers."""
        timer_names = list(self._timers.keys())
        for name in timer_names:
            self.stop_timer(name)
        self._logger.debug("All timers stopped", count=len(timer_names))


class SupsrcTuiApp(App):
    """A stabilized Textual app to monitor supsrc repositories."""

    TITLE = "Supsrc Watcher"
    SUB_TITLE = "Monitoring filesystem events..."
    BINDINGS = [
        ("d", "toggle_dark", "Toggle Dark Mode"),
        ("q", "quit", "Quit Application"),
        ("ctrl+l", "clear_log", "Clear Log"),
        ("enter", "select_repo_for_detail", "View Details"),
        ("escape", "hide_detail_pane", "Hide Details"),
        ("r", "refresh_details", "Refresh Details"),
        ("tab", "focus_next", "Next Panel"),
        ("shift+tab", "focus_previous", "Previous Panel"),
    ]

    # Updated CSS for better layout
    CSS = """
    Screen {
        layout: vertical;
        overflow: hidden;
    }

    #repository_pane_container { /* Was #table_container */
        height: 40%; /* Initial height, can be adjusted by watch_show_detail_pane */
        overflow-y: auto;
        scrollbar-gutter: stable;
        border: round $accent;
        padding: 1;
        margin: 1;
    }

    #detail_pane_container { /* Was #detail_container */
        display: none; /* Hidden by default */
        height: 30%; /* Height when visible, adjusted by watch_show_detail_pane */
        overflow-y: auto;
        scrollbar-gutter: stable;
        border: round $accent;
        padding: 1;
        margin: 1;
    }

    #global_log_container { /* Was #log_container */
        height: 1fr; /* Takes remaining space */
        overflow-y: auto;
        scrollbar-gutter: stable;
        border: round $accent;
        padding: 1;
        margin: 1;
    }

    #status_container {
        height: 3; /* Fixed height for status messages */
        border: round $accent;
        padding: 1;
        margin: 1;
    }

    DataTable > .datatable--header {
        background: $accent-darken-2;
        color: $text;
    }

    DataTable > .datatable--cursor {
        background: $accent;
        color: $text;
    }

    /* .panel-title can be removed if no longer used, or kept if it is.
       For now, I'll keep it commented out as its usage is unclear
       in the new layout. If it was used for titles within the old #left_panel
       or #right_panel, it might not be needed directly on these new containers.
    .panel-title {
        text-style: bold;
        color: $accent;
    }
    */
    """

    # Reactive variables
    repo_states_data: dict[str, Any] = var({})
    show_detail_pane: bool = var(False)
    selected_repo_id: str | None = var(None)

    def __init__(
        self,
        config_path: Path,
        cli_shutdown_event: asyncio.Event,
        **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._config_path = config_path
        self._orchestrator: WatchOrchestrator | None = None
        self._shutdown_event = asyncio.Event()
        self._cli_shutdown_event = cli_shutdown_event
        self._worker: Worker | None = None
        self._timer_manager = TimerManager(self)
        self._is_shutting_down = False

    def compose(self) -> ComposeResult:
        """Compose the TUI layout with improved structure."""
        yield Header()

        # Repositories Table (Top)
        with Container(id="repository_pane_container"):
            yield DataTable(id="repo-table", zebra_stripes=True)

        # Repository Details (Middle, initially hidden)
        # This container's display style will be controlled by `watch_show_detail_pane`
        with Container(id="detail_pane_container"):
            yield TextualLog(id="repo_detail_log", highlight=False)

        # Global Event Log (Bottom)
        with Container(id="global_log_container"):
            yield TextualLog(id="event-log", highlight=True, max_lines=1000)

        # Status Log (just above Footer or integrated if simple enough)
        # For now, place it in its own container above the footer.
        with Container(id="status_container"):
            yield TextualLog(id="status_log", highlight=False, max_lines=3)

        yield Footer()

    def on_mount(self) -> None:
        """Initialize the TUI with proper error handling."""
        try:
            log.info("TUI Mounted. Initializing UI components.")
            self._update_sub_title("Initializing...")

            # Initialize table
            table = self.query_one(DataTable)
            table.cursor_type = "row"
            table.add_columns(
                "Status", "Repository", "Last Change",
                "Rule", "Current Action", "Last Commit / Message"
            )

            # Initialize logs
            log_widget = self.query_one("#event-log", TextualLog)
            log_widget.wrap = True
            log_widget.markup = True

            repo_detail_log_widget = self.query_one("#repo_detail_log", TextualLog)
            repo_detail_log_widget.wrap = True

            status_log_widget = self.query_one("#status_log", TextualLog)
            status_log_widget.write_line("[bold green]Supsrc TUI Started[/]")
            status_log_widget.write_line("Press [bold]Tab[/] to navigate, [bold]Enter[/] for details, [bold]Q[/] to quit")

            # Start orchestrator worker
            log.info("Starting orchestrator worker...")
            self._worker = self.run_worker(
                self._run_orchestrator,
                thread=True,
                group="orchestrator"
            )

            # Start shutdown check timer
            self._timer_manager.create_timer(
                "shutdown_check",
                0.5,
                self._check_external_shutdown_sync, # Updated callback
                repeat=True
            )

            self._update_sub_title("Monitoring...")

            # --- ADD DIAGNOSTIC ---
            log.debug("TUI on_mount: Posting a test StateUpdate message to self.")
            # Ensure necessary imports are present for RepositoryState and RepositoryStatus
            # These might need to be added at the top of the file if not already there:
            # from supsrc.state import RepositoryState, RepositoryStatus
            # (Worker should check and add if missing)
            # from supsrc.state import RepositoryState, RepositoryStatus # Explicitly add for clarity for the worker -> This is now added above.
            test_repo_states: RepositoryStatesMap = {
                "test-repo": RepositoryState(repo_id="test-repo", status=RepositoryStatus.IDLE)
            }
            self.post_message(StateUpdate(test_repo_states))
            log.debug("TUI on_mount: Test StateUpdate message posted.")
            # --- END DIAGNOSTIC ---

        except Exception as e:
            log.exception("Error during TUI mount")
            self._update_sub_title(f"Initialization Error: {e}")

    async def _run_orchestrator(self) -> None:
        """Run the orchestrator with comprehensive error handling."""
        log.info("Orchestrator worker started.")
        try:
            self._orchestrator = WatchOrchestrator(
                self._config_path,
                self._shutdown_event,
                app=self
            )
            await self._orchestrator.run()
        except Exception as e:
            log.exception("Orchestrator failed within TUI worker")
            if not self._is_shutting_down:
                self.call_later(
                    self.post_message,
                    LogMessageUpdate(None, "CRITICAL", f"Orchestrator CRASHED: {e}")
                )
                self._update_sub_title("Orchestrator CRASHED!")
                # Auto-quit on orchestrator failure
                await asyncio.sleep(1.0)
                self.call_later(self.action_quit)
        finally:
            log.info("Orchestrator worker finished.")

    async def _check_external_shutdown_async(self) -> None: # Renamed
        """Async part of the shutdown check: performs actual shutdown actions."""
        # This part remains async: logging, subtitle update, and action_quit
        log.warning("External shutdown detected (CLI signal). Processing async actions.")
        self._update_sub_title("Shutdown requested...")
        await self.action_quit()

    def _check_external_shutdown_sync(self) -> None: # New synchronous method
        """
        Synchronous callback for the timer.
        Checks for shutdown conditions and schedules the async part if needed.
        """
        if (self._cli_shutdown_event.is_set() and
            not self._shutdown_event.is_set() and
            not self._is_shutting_down):
            # If conditions met, create a task for the async operations
            asyncio.create_task(self._check_external_shutdown_async())

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Handle worker state changes."""
        log.debug(
            "Worker state changed",
            worker=event.worker.name,
            state=event.state
        )
        if (event.worker == self._worker and
            event.state in ("SUCCESS", "ERROR") and
            not self._is_shutting_down):

            log.info(f"Orchestrator worker stopped: {event.state}")
            self.call_later(self.action_quit)

    async def _fetch_repo_details_worker(self, repo_id: str) -> None:
        """Worker to fetch repository details."""
        if not self._orchestrator:
            return

        try:
            log.debug(f"Fetching details for {repo_id}")
            details = await self._orchestrator.get_repository_details(repo_id)
            self.post_message(RepoDetailUpdate(repo_id, details))
        except Exception as e:
            log.error(f"Error fetching repo details for {repo_id}", error=str(e))
            error_details = {
                "commit_history": [f"[bold red]Error loading details: {e}[/]"]
            }
            self.post_message(RepoDetailUpdate(repo_id, error_details))

    # Watch Methods
    def watch_show_detail_pane(self, show_detail: bool) -> None:
        """Update layout when detail pane visibility changes."""
        try:
            detail_pane_container = self.query_one("#detail_pane_container", Container) # New ID

            if show_detail:
                detail_pane_container.styles.display = "block"
            else:
                detail_pane_container.styles.display = "none"
        except Exception as e:
            log.error("Error updating detail pane visibility", error=str(e)) # Updated log message

    # Action Methods
    def action_select_repo_for_detail(self) -> None:
        """Show detail pane for the selected repository."""
        try:
            table = self.query_one(DataTable)
            row_key = table.get_row_key(table.cursor_row)
            if row_key is not None:
                self.selected_repo_id = str(row_key)
                self.show_detail_pane = True

                if self._orchestrator and self.selected_repo_id:
                    detail_log = self.query_one("#repo_detail_log", TextualLog)
                    detail_log.clear()
                    detail_log.write_line(
                        f"Fetching details for [b]{self.selected_repo_id}[/b]..."
                    )

                    self.run_worker(
                        self._fetch_repo_details_worker(self.selected_repo_id),
                        thread=True,
                        group="repo_detail_fetcher",
                        name=f"fetch_details_{self.selected_repo_id}"
                    )
        except Exception as e:
            log.error("Error selecting repo for detail", error=str(e))

    def action_hide_detail_pane(self) -> None:
        """Hide the detail pane."""
        if self.show_detail_pane:
            self.show_detail_pane = False
            self.selected_repo_id = None
            try:
                self.query_one("#repo_detail_log", TextualLog).clear()
                self.query_one(DataTable).focus()
            except Exception as e:
                log.error("Error hiding detail pane", error=str(e))

    def action_refresh_details(self) -> None:
        """Refresh the current detail view."""
        if self.show_detail_pane and self.selected_repo_id:
            self.action_select_repo_for_detail()

    def action_toggle_dark(self) -> None:
        """Toggle dark mode."""
        try:
            self.screen.dark = not self.screen.dark
        except Exception as e:
            log.error("Failed to toggle dark mode", error=str(e))

    def action_clear_log(self) -> None:
        """Clear the event log."""
        try:
            self.query_one("#event-log", TextualLog).clear()
            self.post_message(LogMessageUpdate(None, "INFO", "Log cleared."))
        except Exception as e:
            log.error("Failed to clear TUI log", error=str(e))

    async def action_quit(self) -> None:
        """Quit the application gracefully."""
        log.info("action_quit invoked.") # ADD THIS VERY FIRST
        if self._is_shutting_down:
            return

        self._is_shutting_down = True
        log.info("Quit action triggered.") # Original log.info kept for sequence confirmation
        self._update_sub_title("Quitting...")

        # Capture worker instance before any awaits that allow context switching
        worker_to_cancel = self._worker

        try:
            # Signal orchestrator shutdown
            if not self._shutdown_event.is_set():
                self._shutdown_event.set()

            # Stop all timers
            self._timer_manager.stop_all_timers()

            # Give worker time to react (and other tasks to run)
            await asyncio.sleep(0.3)

            # Cancel worker if it was valid and is still running
            if worker_to_cancel and worker_to_cancel.is_running:
                log.info("Cancelling orchestrator worker...", worker_name=getattr(worker_to_cancel, 'name', 'Unknown'))
                try:
                    await worker_to_cancel.cancel()
                except Exception as e:
                    log.error("Error cancelling worker", worker_name=getattr(worker_to_cancel, 'name', 'Unknown'), error=str(e), exc_info=True)
            elif worker_to_cancel: # Worker existed but was not running
                log.info("Orchestrator worker existed but was not running.", worker_name=getattr(worker_to_cancel, 'name', 'Unknown'), worker_state=getattr(worker_to_cancel, 'state', 'Unknown'))
            else: # Worker was None to begin with
                log.info("Orchestrator worker was None, no cancellation needed.")

            log.info("Exiting TUI application.")
            self.exit(0)

        except Exception:
            log.exception("Error during quit action")
            self.exit(1)

    # Message Handlers
    def on_state_update(self, message: StateUpdate) -> None:
        """Handle repository state updates."""
        log.debug(
            "TUI on_state_update received",
            num_states=len(message.repo_states),
            repo_ids=list(message.repo_states.keys())
        )
        try:
            # The original debug log has been replaced by the more structured one above.
            # If the repr(message.repo_states) is still desired for deep debugging,
            # it could be added here or a separate log line. For now, it's removed
            # to avoid redundancy with the new structured log.
            # debug_message_content = repr(message.repo_states)
            # log.debug(f"DEBUG_TUI_APP: on_state_update received: {debug_message_content}")

            table = self.query_one(DataTable)
            current_keys = set(table.rows.keys())
            incoming_keys = set(message.repo_states.keys())

            # Remove obsolete rows
            for key_to_remove in current_keys - incoming_keys:
                if table.is_valid_row_key(key_to_remove):
                    table.remove_row(key_to_remove)

            # Update/add rows
            for repo_id_obj, state in message.repo_states.items():
                repo_id_str = str(repo_id_obj)

                # Format display data
                status_display = state.display_status_emoji
                repository_display = repo_id_str
                last_change_display = (
                    state.last_change_time.strftime("%Y-%m-%d %H:%M:%S")
                    if state.last_change_time else "N/A"
                )

                rule_emoji = state.rule_emoji or ""
                rule_indicator = state.rule_dynamic_indicator or "N/A"
                rule_display = f"{rule_emoji} {rule_indicator}".strip()

                action_display = state.action_description or ""
                if (state.action_description and
                    state.action_progress_total is not None and
                    state.action_progress_completed is not None):

                    total = state.action_progress_total
                    completed = state.action_progress_completed
                    if total > 0:
                        percentage = (completed / total) * 100
                        bar_width = 10
                        filled_width = int(bar_width * completed // total)
                        bar_text = "❚" * filled_width + "-" * (bar_width - filled_width)
                        action_display = (
                            f"{state.action_description} [{bar_text}] {percentage:.0f}%"
                        )

                commit_hash = state.last_commit_short_hash or "-------"
                commit_msg = state.last_commit_message_summary or "No commit info"
                if len(commit_msg) > 30:
                    commit_msg = commit_msg[:27] + "..."
                last_commit_display = f"{commit_hash} - {commit_msg}"

                row_data = (
                    status_display,
                    repository_display,
                    last_change_display,
                    rule_display,
                    action_display,
                    last_commit_display
                )

                if repo_id_str in table.rows:
                    table.update_row(repo_id_str, *row_data, update_width=False)
                else:
                    table.add_row(*row_data, key=repo_id_str)

        except Exception as e:
            log.error("Failed to update TUI table", error=str(e))

    def on_log_message_update(self, message: LogMessageUpdate) -> None:
        """Handle log message updates."""
        try:
            log_widget = self.query_one("#event-log", TextualLog)
            # The message.message from TextualLogHandler should now be pre-formatted
            # with Rich markup by the ConsoleRenderer.
            log_widget.write_line(message.message)
        except Exception as e:
            # Using the app's own logger here is fine for TUI-specific errors.
            log.error("Failed to write to TUI log widget", error=str(e), raw_message_level=message.level, raw_message_content=message.message)

    def on_repo_detail_update(self, message: RepoDetailUpdate) -> None:
        """Handle repository detail updates."""
        if (self.show_detail_pane and
            message.repo_id == self.selected_repo_id):

            try:
                detail_log = self.query_one("#repo_detail_log", TextualLog)
                detail_log.clear()

                commit_history = message.details.get("commit_history", [])
                if not commit_history:
                    detail_log.write_line("No commit history found or an error occurred.")
                else:
                    detail_log.write_line(f"[b]Commit History for {message.repo_id}:[/b]\n")
                    for entry in commit_history:
                        detail_log.write_line(entry)
            except Exception as e:
                log.error("Error updating repo details", error=str(e))

    # Helper Methods
    def _update_sub_title(self, text: str) -> None:
        """Update subtitle safely."""
        try:
            self.sub_title = text
        except Exception as e:
            log.warning("Failed to update TUI sub-title", error=str(e))

    def _get_level_style(self, level_name: str) -> str:
        """Get style for log level."""
        level = level_name.upper()
        styles = {
            "CRITICAL": "bold white on red",
            "ERROR": "bold red",
            "WARNING": "yellow",
            "INFO": "green",
            "DEBUG": "dim blue",
            "SUCCESS": "bold green"
        }
        return styles.get(level, "white")

# 🖥️✨

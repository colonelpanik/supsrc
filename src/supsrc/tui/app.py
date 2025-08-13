#
# src/supsrc/tui/app.py
#
"""
Stabilized TUI application with improved layout and proper timer management.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import structlog
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.reactive import var
from textual.timer import Timer
from textual.widgets import DataTable, Footer, Header
from textual.widgets import Log as TextualLog
from textual.worker import Worker, WorkerState

from supsrc.runtime.orchestrator import WatchOrchestrator
from supsrc.state import RepositoryStatus
from supsrc.tui.messages import LogMessageUpdate, RepoDetailUpdate, StateUpdate

log = structlog.get_logger("tui.app")


def get_countdown_display(seconds_left: int | None) -> str:
    """Generate countdown display with hand emojis for last 10 seconds."""
    if seconds_left is None:
        return ""

    if seconds_left > 10:
        # Show regular countdown
        minutes = seconds_left // 60
        secs = seconds_left % 60
        if minutes > 0:
            return f"{minutes}:{secs:02d}"
        else:
            return f"{secs}s"
    elif seconds_left == 10:
        return "🙌"  # Both hands open (10)
    elif seconds_left == 9:
        return "🖐️✋"  # 5 + 4
    elif seconds_left == 8:
        return "✋✌️"  # 5 + 3
    elif seconds_left == 7:
        return "✋🤘"  # 5 + 2
    elif seconds_left == 6:
        return "✋☝️"  # 5 + 1
    elif seconds_left == 5:
        return "🖐️"  # One hand (5)
    elif seconds_left == 4:
        return "🖖"  # Four fingers
    elif seconds_left == 3:
        return "🤟"  # Three fingers
    elif seconds_left == 2:
        return "✌️"  # Peace sign (2)
    elif seconds_left == 1:
        return "☝️"  # One finger
    else:
        return "💥"  # Zero/trigger


def format_last_commit_time(last_change_time, threshold_hours=3):
    """Format last commit time as relative or absolute based on age."""
    if not last_change_time:
        return "Never"

    now = datetime.now(UTC)
    delta = now - last_change_time
    total_seconds = int(delta.total_seconds())

    # If older than threshold, show full date
    if delta.total_seconds() > (threshold_hours * 3600):
        return last_change_time.strftime("%Y-%m-%d %H:%M:%S")

    # Otherwise show relative time
    if total_seconds < 60:
        return f"{total_seconds}s ago"
    elif total_seconds < 3600:
        minutes = total_seconds // 60
        return f"{minutes}m ago"
    else:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        if minutes > 0:
            return f"{hours}h {minutes}m ago"
        else:
            return f"{hours}h ago"


class TimerManager:
    """Manages application timers with proper lifecycle handling."""

    def __init__(self, app: "SupsrcTuiApp") -> None:
        self.app = app
        self._timers: dict[str, Timer] = {}
        self._logger = log.bind(component="TimerManager")

    def create_timer(
        self, name: str, interval: float, callback: callable, repeat: bool = True
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
        success = True
        try:
            # Check if the timer is active by inspecting its internal handle
            if hasattr(timer, "_Timer__handle") and timer._Timer__handle is not None:
                timer.stop()
        except Exception as e:
            self._logger.error("Error stopping timer", name=name, error=str(e))
            success = False
        finally:
            if name in self._timers:
                del self._timers[name]
            self._logger.debug("Timer stopped or already inactive", name=name)
        return success

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
    BINDINGS: ClassVar[list] = [
        ("d", "toggle_dark", "Toggle Dark Mode"),
        ("q", "quit", "Quit Application"),
        ("ctrl+c", "quit", "Quit Application"),
        ("ctrl+l", "clear_log", "Clear Log"),
        ("enter", "select_repo_for_detail", "View Details"),
        ("escape", "hide_detail_pane", "Hide Details"),
        ("r", "refresh_details", "Refresh Details"),
        ("p", "pause_monitoring", "Pause/Resume All"),
        ("s", "suspend_monitoring", "Suspend/Resume All"),
        ("c", "reload_config", "Reload Config"),
        ("h", "show_help", "Show Help"),
        ("tab", "focus_next", "Next Panel"),
        ("shift+tab", "focus_previous", "Previous Panel"),
        ("P", "toggle_repo_pause", "Toggle Repo Pause"),
        ("S", "toggle_repo_stop", "Toggle Repo Stop"),
        ("shift+R", "refresh_repo_status", "Refresh Repo Status"),
        ("G", "resume_repo_monitoring", "Resume Repo Monitoring"),
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

    /* Status emoji column - fixed width */
    DataTable > .datatable--column-0 {
        width: 1;
        min-width: 2;
        max-width: 2;
    }

    /* Make branch column shrinkable on small terminals */
    DataTable > .datatable--column-3 {
        width: 1fr;
        min-width: 10;
        max-width: 25;
    }

    /* Ensure Repository column gets priority space */
    DataTable > .datatable--column-2 {
        width: 2fr;
        min-width: 20;
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

    def __init__(self, config_path: Path, cli_shutdown_event: asyncio.Event, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._config_path = config_path
        self._orchestrator: WatchOrchestrator | None = None
        self._shutdown_event = asyncio.Event()
        self._cli_shutdown_event = cli_shutdown_event
        self._worker: Worker | None = None
        self._timer_manager = TimerManager(self)
        self._is_shutting_down = False
        self._is_paused = False
        self._is_suspended = False

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
                "📊",  # Action/Status emoji header
                "⏱️",   # Timer/countdown column
                "Repository",
                "Branch",  # New branch column
                "📁",  # Total files
                "📝",  # Changed files count
                "➕",  # Added files  # noqa: RUF001
                "➖",  # Deleted files  # noqa: RUF001
                "✏️",  # Modified files
                "Last Commit",  # Moved after modified
                "Rule",
            )

            # Initialize logs
            log_widget = self.query_one("#event-log", TextualLog)
            log_widget.wrap = True
            log_widget.markup = True

            repo_detail_log_widget = self.query_one("#repo_detail_log", TextualLog)
            repo_detail_log_widget.wrap = True

            status_log_widget = self.query_one("#status_log", TextualLog)
            status_log_widget.write_line("[bold green]Supsrc TUI Started[/]")
            status_log_widget.write_line(
                "Press [bold]Tab[/] to navigate, [bold]Enter[/] for details, [bold]Q[/] to quit"
            )

            # Start orchestrator worker in the main event loop
            log.info("Starting orchestrator worker...")
            self._worker = self.run_worker(
                self._run_orchestrator, name="Orchestrator", group="orchestrator"
            )

            # Start shutdown check timer
            self._timer_manager.create_timer(
                "shutdown_check",
                0.5,
                self._check_external_shutdown,
                repeat=True,
            )

            # Start countdown update timer
            self._timer_manager.create_timer(
                "countdown_update",
                1.0,
                self._update_countdown_display,
                repeat=True,
            )

            self._update_sub_title("Monitoring...")

        except Exception as e:
            log.exception("Error during TUI mount")
            self._update_sub_title(f"Initialization Error: {e}")

    async def _run_orchestrator(self) -> None:
        """Run the orchestrator with comprehensive error handling."""
        log.info("Orchestrator worker started.")
        try:
            self._orchestrator = WatchOrchestrator(
                self._config_path, self._shutdown_event, app=self
            )
            await self._orchestrator.run()
        except asyncio.CancelledError:
            log.info("Orchestrator worker was cancelled gracefully.")
        except Exception:
            # The worker state change handler is now responsible for the reaction.
            # Just log the exception here. The TUI will be shut down by the handler.
            log.exception("Orchestrator failed within TUI worker. The app will shut down.")
        finally:
            log.info("Orchestrator worker finished.")

    def _check_external_shutdown(self) -> None:
        """Check for external shutdown signals and quit if detected."""
        if self._cli_shutdown_event.is_set() and not self._is_shutting_down:
            log.warning("External shutdown detected (CLI signal). Triggering quit.")
            self.action_quit()

    def _update_countdown_display(self) -> None:
        """Update countdown displays for all repositories."""
        try:
            if hasattr(self, "_orchestrator") and self._orchestrator:
                # Update countdown for each repository state
                for repo_state in self._orchestrator.repo_states.values():
                    repo_state.update_timer_countdown()

                # Trigger a state update to refresh the display with actual state objects
                self.post_message(StateUpdate(self._orchestrator.repo_states))
        except Exception as e:
            log.debug(f"Error updating countdown: {e}")

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Handle worker state changes, and exit the app when the main worker is done."""
        log.debug("Worker state changed", worker=event.worker.name, state=event.state)
        # Only act on terminal states: SUCCESS or ERROR. Ignore PENDING and RUNNING.
        if event.worker == self._worker and event.state in (WorkerState.SUCCESS, WorkerState.ERROR):
            log.info(
                f"Orchestrator worker has finished with state: {event.state!r}.",
            )
            # If the app is already shutting down, this is expected. We can exit cleanly.
            if self._is_shutting_down:
                self.exit(0)
            else:
                # If the app was NOT shutting down, this is an unexpected crash.
                log.error("Orchestrator worker stopped unexpectedly. Initiating shutdown.")
                self.post_message(
                    LogMessageUpdate(None, "CRITICAL", "Orchestrator worker crashed. TUI will now exit.")
                )
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
            error_details = {"commit_history": [f"[bold red]Error loading details: {e}[/]"]}
            self.post_message(RepoDetailUpdate(repo_id, error_details))

    # Watch Methods
    def watch_show_detail_pane(self, show_detail: bool) -> None:
        """Update layout when detail pane visibility changes."""
        try:
            detail_pane_container = self.query_one("#detail_pane_container", Container)  # New ID

            if show_detail:
                detail_pane_container.styles.display = "block"
            else:
                detail_pane_container.styles.display = "none"
        except Exception as e:
            log.error("Error updating detail pane visibility", error=str(e))  # Updated log message

    # Action Methods
    def action_select_repo_for_detail(self) -> None:
        """Show detail pane for the selected repository."""
        try:
            table = self.query_one(DataTable)
            # Get the row key using coordinate_to_cell_key
            try:
                cell_key = table.coordinate_to_cell_key((table.cursor_row, 0))
                row_key = cell_key.row_key
                self.selected_repo_id = str(row_key.value) if row_key else None
            except Exception:
                self.selected_repo_id = None

            if self.selected_repo_id:
                self.show_detail_pane = True

                if self._orchestrator and self.selected_repo_id:
                    detail_log = self.query_one("#repo_detail_log", TextualLog)
                    detail_log.clear()
                    detail_log.write_line(f"Fetching details for [b]{self.selected_repo_id}[/b]...")

                    self.run_worker(
                        self._fetch_repo_details_worker(self.selected_repo_id),
                        thread=True,
                        group="repo_detail_fetcher",
                        name=f"fetch_details_{self.selected_repo_id}",
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
            self.dark = not self.dark
        except Exception as e:
            log.error("Failed to toggle dark mode", error=str(e))

    def action_clear_log(self) -> None:
        """Clear the event log."""
        try:
            self.query_one("#event-log", TextualLog).clear()
            self.post_message(LogMessageUpdate(None, "INFO", "Log cleared."))
        except Exception as e:
            log.error("Failed to clear TUI log", error=str(e))

    def action_pause_monitoring(self) -> None:
        """Toggle pause state for monitoring."""
        self._is_paused = not self._is_paused

        if self._is_paused:
            self._update_sub_title("⏸️  Monitoring PAUSED")
            self.post_message(
                LogMessageUpdate(None, "WARNING", "⏸️  Monitoring PAUSED - Press 'p' to resume")
            )
            # Tell orchestrator to pause
            if self._orchestrator:
                self._orchestrator.pause_monitoring()
        else:
            self._update_sub_title("▶️  Monitoring RESUMED")
            self.post_message(LogMessageUpdate(None, "INFO", "▶️  Monitoring RESUMED"))
            # Tell orchestrator to resume
            if self._orchestrator:
                self._orchestrator.resume_monitoring()

    def action_suspend_monitoring(self) -> None:
        """Suspend monitoring (stronger than pause)."""
        if not self._is_suspended:
            self._is_suspended = True
            self._update_sub_title("⏹️  Monitoring SUSPENDED")
            self.post_message(
                LogMessageUpdate(None, "WARNING", "⏹️  Monitoring SUSPENDED - Press 's' to resume")
            )
            # Tell orchestrator to suspend
            if self._orchestrator:
                self._orchestrator.suspend_monitoring()
        else:
            self._is_suspended = False
            self._update_sub_title("▶️  Monitoring RESUMED from suspension")
            self.post_message(
                LogMessageUpdate(None, "INFO", "▶️  Monitoring RESUMED from suspension")
            )
            # Tell orchestrator to resume from suspension
            if self._orchestrator:
                self._orchestrator.resume_monitoring()

    def action_reload_config(self) -> None:
        """Reload configuration file."""
        self.post_message(LogMessageUpdate(None, "INFO", "🔄 Reloading configuration..."))
        if self._orchestrator:
            # Create async task to reload config
            async def _reload():
                success = await self._orchestrator.reload_config()
                if success:
                    self.post_message(
                        LogMessageUpdate(None, "SUCCESS", "✅ Configuration reloaded successfully")
                    )
                else:
                    self.post_message(
                        LogMessageUpdate(None, "ERROR", "❌ Configuration reload failed")
                    )

            asyncio.create_task(_reload())  # noqa: RUF006

    def action_show_help(self) -> None:
        """Show help information about emojis and shortcuts."""
        help_text = """
🔄 EMOJI MEANINGS:
  ⏹️  - Stopped (not monitored)
  ⏸️  - Paused (monitoring, no commits)
  ▶️  - Running/Active
  🔄 - Processing/Reloading
  ⏳ - Inactivity timer running
  ⏲️  - Timer active
  ✅ - Success/Completed
  ❌ - Error/Failed
  ⚠️  - Warning
  💾 - Committing changes
  🚀 - Pushing to remote
  ⏱️  - Timing/Duration
  🔼⚙️ - Auto-commit marker

📋 KEYBOARD SHORTCUTS:
  h     - Show this help
  p     - Pause/Resume ALL monitoring
  s     - Suspend/Resume ALL monitoring (stops watchers)
  c     - Reload configuration (90s pause)
  d     - Toggle dark mode
  q     - Quit application
  Ctrl+C - Quit application
  Ctrl+L - Clear event log
  Enter  - View repository details
  Escape - Hide details pane
  r     - Refresh repository details
  Tab    - Focus next panel
  Shift+Tab - Focus previous panel
  P     - Toggle selected repo pause
  S     - Toggle selected repo stop
  Shift+R - Refresh selected repo status
  G     - Resume selected repo monitoring

💡 NOTES:
  • Pause keeps watchers active but queues events
  • Suspend stops watchers completely
  • Config reload pauses for 90 seconds
  • Use 'p' to quickly pause/resume ALL
"""
        self.post_message(LogMessageUpdate(None, "INFO", help_text))

    def _get_selected_repo_id(self) -> str | None:
        """Helper to get the ID of the currently selected repository."""
        try:
            table = self.query_one(DataTable)
            cell_key = table.coordinate_to_cell_key((table.cursor_row, 0))
            row_key = cell_key.row_key
            return str(row_key.value) if row_key else None
        except Exception:
            return None

    async def action_toggle_repo_pause(self) -> None:
        """Toggle pause state for the selected repository."""
        repo_id = self._get_selected_repo_id()
        if not repo_id or not self._orchestrator:
            self.post_message(LogMessageUpdate(None, "WARNING", "No repository selected or orchestrator not ready."))
            return

        success = self._orchestrator.toggle_repository_pause(repo_id)
        if success:
            # Force an immediate state update to ensure UI reflects the change
            self._orchestrator._post_tui_state_update()

            repo_state = self._orchestrator.repo_states.get(repo_id)
            if repo_state and repo_state.is_paused:
                self.post_message(LogMessageUpdate(None, "INFO", f"⏸️ Repository '{repo_id}' paused."))
            else:
                self.post_message(LogMessageUpdate(None, "INFO", f"▶️ Repository '{repo_id}' resumed."))
        else:
            self.post_message(LogMessageUpdate(None, "ERROR", f"Failed to toggle pause for '{repo_id}'."))

    async def action_toggle_repo_stop(self) -> None:
        """Toggle stop state for the selected repository."""
        repo_id = self._get_selected_repo_id()
        if not repo_id or not self._orchestrator:
            self.post_message(LogMessageUpdate(None, "WARNING", "No repository selected or orchestrator not ready."))
            return

        success = await self._orchestrator.toggle_repository_stop(repo_id)
        if success:
            repo_state = self._orchestrator.repo_states.get(repo_id)
            if repo_state and repo_state.is_stopped:
                self.post_message(LogMessageUpdate(None, "INFO", f"⏹️ Repository '{repo_id}' stopped from monitoring."))
            else:
                self.post_message(LogMessageUpdate(None, "INFO", f"▶️ Repository '{repo_id}' resumed monitoring."))
        else:
            self.post_message(LogMessageUpdate(None, "ERROR", f"Failed to toggle stop for '{repo_id}'."))

    async def action_refresh_repo_status(self) -> None:
        """Force refresh status for the selected repository."""
        repo_id = self._get_selected_repo_id()
        if not repo_id or not self._orchestrator:
            self.post_message(LogMessageUpdate(None, "WARNING", "No repository selected or orchestrator not ready."))
            return

        self._orchestrator.set_repo_refreshing_status(repo_id, True)
        self.post_message(LogMessageUpdate(None, "INFO", f"🔄 Refreshing status for '{repo_id}'..."))
        success = await self._orchestrator.refresh_repository_status(repo_id)
        self._orchestrator.set_repo_refreshing_status(repo_id, False)
        if success:
            self.post_message(LogMessageUpdate(None, "INFO", f"✅ Status for '{repo_id}' refreshed."))
        else:
            self.post_message(LogMessageUpdate(None, "ERROR", f"❌ Failed to refresh status for '{repo_id}'."))

    async def action_resume_repo_monitoring(self) -> None:
        """Resume monitoring for the selected repository (unpause/unstop)."""
        repo_id = self._get_selected_repo_id()
        if not repo_id or not self._orchestrator:
            self.post_message(LogMessageUpdate(None, "WARNING", "No repository selected or orchestrator not ready."))
            return

        success = await self._orchestrator.resume_repository_monitoring(repo_id)
        if success:
            self.post_message(LogMessageUpdate(None, "INFO", f"▶️ Repository '{repo_id}' resumed monitoring."))
        else:
            self.post_message(LogMessageUpdate(None, "ERROR", f"Failed to resume monitoring for '{repo_id}'."))

    def action_quit(self) -> None:
        """Initiates a graceful shutdown of the application."""
        if self._is_shutting_down:
            return  # Shutdown already in progress

        self._is_shutting_down = True
        log.info("Quit action triggered. Signaling components and exiting.")
        self._update_sub_title("Quitting...")

        # Stop all managed timers immediately.
        self._timer_manager.stop_all_timers()

        # Signal other components that need to know about the shutdown.
        if not self._shutdown_event.is_set():
            self._shutdown_event.set()
        if not self._cli_shutdown_event.is_set():
            self._cli_shutdown_event.set()

        # The most robust way to shut down is to tell Textual to exit.
        # Textual's exit sequence will automatically handle cancelling any
        # running workers (like our orchestrator worker).
        self.exit(0)

    # Message Handlers
    def on_state_update(self, message: StateUpdate) -> None:
        """Handle repository state updates."""
        log.debug(
            "TUI on_state_update received",
            num_states=len(message.repo_states),
            repo_ids=list(message.repo_states.keys()),
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
                try:  # noqa: SIM105
                    table.remove_row(key_to_remove)
                except Exception:
                    # Row may have already been removed
                    pass

            # Update/add rows
            for repo_id_obj, state in message.repo_states.items():
                repo_id_str = str(repo_id_obj)

                # Format display data
                status_display = state.display_status_emoji
                # Debug log to see the state
                log.debug(
                    f"Repository {repo_id_str} - emoji: '{status_display}', "
                    f"paused: {state.is_paused}, stopped: {state.is_stopped}, "
                    f"status: {state.status.name}"
                )
                timer_display = get_countdown_display(state.timer_seconds_left)
                repository_display = repo_id_str

                # Use relative time for recent changes, full date for older ones
                # Get threshold from config if available
                threshold = 3.0  # default
                if hasattr(self, "_orchestrator") and self._orchestrator and self._orchestrator.config:
                    threshold = getattr(
                        self._orchestrator.config.global_config,
                        "last_change_threshold_hours",
                        3.0
                    )
                # Use actual Git commit timestamp if available, fallback to last_change_time
                timestamp = state.last_commit_timestamp or state.last_change_time
                last_change_display = format_last_commit_time(timestamp, threshold)

                rule_emoji = state.rule_emoji or ""
                rule_indicator = state.rule_dynamic_indicator or "N/A"
                rule_display = f"{rule_emoji} {rule_indicator}".strip()

                # Format file statistics with color based on commit status
                # Show loading indicator for repos that haven't been initialized yet
                if state.total_files == 0 and not state.has_uncommitted_changes and state.status == RepositoryStatus.IDLE:
                    # Likely still loading
                    total_files_display = "[dim]...[/dim]"
                elif state.total_files == 0:
                    # Show a question mark for 0 files after loading is complete
                    total_files_display = "[bold red]?[/bold red]"
                else:
                    total_files_display = str(state.total_files)

                if state.has_uncommitted_changes:
                    # Active colors for uncommitted changes
                    changed_files_display = f"[bold yellow]{state.changed_files}[/bold yellow]" if state.changed_files > 0 else "0"
                    added_display = f"[bold green]{state.added_files}[/bold green]" if state.added_files > 0 else "0"
                    deleted_display = f"[bold red]{state.deleted_files}[/bold red]" if state.deleted_files > 0 else "0"
                    modified_display = f"[bold blue]{state.modified_files}[/bold blue]" if state.modified_files > 0 else "0"
                else:
                    # Grey/dim for committed state
                    changed_files_display = f"[dim]{state.changed_files}[/dim]" if state.changed_files > 0 else "0"
                    added_display = f"[dim]{state.added_files}[/dim]" if state.added_files > 0 else "0"
                    deleted_display = f"[dim]{state.deleted_files}[/dim]" if state.deleted_files > 0 else "0"
                    modified_display = f"[dim]{state.modified_files}[/dim]" if state.modified_files > 0 else "0"

                # Get branch display - truncate from beginning if too long
                branch_name = state.current_branch or "main"
                branch_display = "..." + branch_name[-17:] if len(branch_name) > 20 else branch_name

                row_data = (
                    status_display,
                    timer_display,
                    repository_display,
                    branch_display,  # New branch column
                    total_files_display,
                    changed_files_display,
                    added_display,
                    deleted_display,
                    modified_display,
                    last_change_display,  # Moved after modified
                    rule_display,
                )

                if repo_id_str in table.rows:
                    # Save cursor position before update
                    cursor_row = table.cursor_row
                    cursor_column = table.cursor_column

                    # Update existing row by removing and re-adding
                    table.remove_row(repo_id_str)
                    table.add_row(*row_data, key=repo_id_str)

                    # Restore cursor position if it's still valid
                    if cursor_row < table.row_count:
                        table.cursor_coordinate = (cursor_row, cursor_column)
                else:
                    table.add_row(*row_data, key=repo_id_str)

        except Exception as e:
            log.error("Failed to update TUI table", error=str(e))

    def on_log_message_update(self, message: LogMessageUpdate) -> None:
        """Handle log message updates."""
        try:
            log_widget = self.query_one("#event-log", TextualLog)
            # Format message with repo name if available
            formatted_message = f"[{message.repo_id}] {message.message}" if message.repo_id else message.message
            log_widget.write_line(formatted_message)
        except Exception as e:
            # Using the app's own logger here is fine for TUI-specific errors.
            log.error(
                "Failed to write to TUI log widget",
                error=str(e),
                raw_message_level=message.level,
                raw_message_content=message.message,
            )

    def on_repo_detail_update(self, message: RepoDetailUpdate) -> None:
        """Handle repository detail updates."""
        if self.show_detail_pane and message.repo_id == self.selected_repo_id:
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
            "SUCCESS": "bold green",
        }
        return styles.get(level, "white")
        
# 🖥️✨

#
# supsrc/tui/app.py
#

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

# --- Textual Imports ---
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.message import Message
from textual.reactive import var
from textual.timer import Timer  # <<< Import Timer
from textual.widgets import DataTable, Footer, Header
from textual.widgets import Log as TextualLog
from textual.worker import Worker

if TYPE_CHECKING:
    Var = var
else:
    Var = object

import structlog

# --- Supsrc Imports ---
from supsrc.runtime.orchestrator import RepositoryStatesMap, WatchOrchestrator
from supsrc.state import RepositoryStatus

log = structlog.get_logger("tui.app")

# --- Custom Messages (Unchanged) ---
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


# --- The Textual Application ---
class SupsrcTuiApp(App):
    """A Textual app to monitor supsrc repositories."""

    TITLE = "Supsrc Watcher"
    SUB_TITLE = "Monitoring filesystem events..."
    BINDINGS = [
        ("d", "toggle_dark", "Toggle Dark Mode"),
        ("q", "quit", "Quit Application"),
        ("ctrl+l", "clear_log", "Clear Log"),
    ]
    CSS = """
    Screen {
        layout: vertical;
        overflow-y: hidden;
    }
    #main-container {
        height: 1fr;
        border: thick $accent;
        overflow-y: hidden; /* Prevent container scroll */
    }
    #repo-table {
        height: 70%; /* Allocate more space to table */
        border-bottom: thick $accent;
        overflow-y: auto;
        scrollbar-gutter: stable;
    }
    #event-log {
        height: 30%; /* Allocate less space to log */
        border: none;
        overflow-y: auto;
        scrollbar-gutter: stable;
    }
    DataTable > .datatable--header {
        background: $accent-darken-2;
        color: $text;
    }
    DataTable > .datatable--cursor {
        background: $accent;
        color: $text;
    }
    """

    if TYPE_CHECKING:
        repo_states_data: Var[dict[str, Any]]
    repo_states_data = var({})

    # __init__ - Add timer attribute
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
        self._shutdown_check_timer: Timer | None = None # <<< Store timer object

    def compose(self) -> ComposeResult:
        # (Implementation unchanged)
        yield Header()
        with Container(id="main-container"):
            yield DataTable(id="repo-table", zebra_stripes=True)
            yield TextualLog(id="event-log", highlight=True, max_lines=1000)
        yield Footer()

    def on_mount(self) -> None:
        # (Implementation unchanged from last correction)
        log.info("TUI Mounted. Initializing UI components.")
        self._update_sub_title("Initializing...")
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("ID", "Status", "Last Change", "Saves", "Error / Last Action")
        log_widget = self.query_one(TextualLog)
        log_widget.wrap = True
        log_widget.markup = True
        log.info("Starting orchestrator worker...")
        self._worker = self.run_worker(self._run_orchestrator, thread=True, group="orchestrator")
        # --- FIX HERE: Store the timer object ---
        self._shutdown_check_timer = self.set_interval(0.5, self._check_external_shutdown, name="ExternalShutdownCheck")
        # --------------------------------------
        self._update_sub_title("Monitoring...")

    # _run_orchestrator remains the same
    async def _run_orchestrator(self) -> None:
        # (Implementation unchanged)
        log.info("Orchestrator worker started.")
        self._orchestrator = WatchOrchestrator(self._config_path, self._shutdown_event, app=self)
        try: await self._orchestrator.run()
        except Exception as e:
             log.exception("Orchestrator failed within TUI worker")
             self.call_later(self.post_message, LogMessageUpdate(None, "CRITICAL", f"Orchestrator CRASHED: {e}"))
             self._update_sub_title("Orchestrator CRASHED!")
        finally:
            log.info("Orchestrator worker finished.")
            if not self._shutdown_event.is_set() and not self._cli_shutdown_event.is_set():
                 log.warning("Orchestrator stopped unexpectedly, requesting TUI quit.")
                 self._update_sub_title("Orchestrator Stopped.")
                 self.call_later(self.action_quit)

    # _check_external_shutdown remains the same
    async def _check_external_shutdown(self) -> None:
         # (Implementation unchanged)
         if self._cli_shutdown_event.is_set() and not self._shutdown_event.is_set():
              log.warning("External shutdown detected (CLI signal), stopping TUI and orchestrator.")
              self._update_sub_title("Shutdown requested...")
              await self.action_quit()

    # on_worker_state_changed remains the same
    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        # (Implementation unchanged)
        log.debug(f"Worker {event.worker.name!r} state changed to {event.state!r}")
        if event.worker == self._worker and event.state in ("SUCCESS", "ERROR"):
             log.info(f"Orchestrator worker stopped with state: {event.state!r}")
             if not self._shutdown_event.is_set() and not self._cli_shutdown_event.is_set():
                 self.call_later(self.action_quit)

    # --- Action Methods ---
    def action_toggle_dark(self) -> None:
        # (Implementation unchanged)
        try: self.screen.dark = not self.screen.dark
        except Exception as e: log.error("Failed to toggle dark mode", error=str(e))
    def action_clear_log(self) -> None:
        # (Implementation unchanged)
        try: self.query_one(TextualLog).clear(); self.post_message(LogMessageUpdate(None, "INFO", "Log cleared."))
        except Exception as e: log.error("Failed to clear TUI log", error=str(e))

    async def action_quit(self) -> None:
        """Action to quit the application."""
        log.info("Quit action triggered."); self._update_sub_title("Quitting...")
        if not self._shutdown_event.is_set(): self._shutdown_event.set() # Signal orchestrator

        # --- FIX HERE: Stop the timer object ---
        if self._shutdown_check_timer:
            try:
                self._shutdown_check_timer.stop()
                log.debug("Stopped external shutdown check timer.")
            except Exception as e:
                log.error("Error stopping shutdown check timer", error=str(e))
        # --------------------------------------

        await asyncio.sleep(0.3) # Give worker time to react
        if self._worker and self._worker.is_running:
             log.info("Attempting to cancel orchestrator worker...")
             try: await self._worker.cancel()
             except Exception: log.exception("Error during worker cancel")
        log.info("Exiting TUI application."); self.exit(0)


    # --- Message Handlers (remain the same) ---
    def on_state_update(self, message: StateUpdate) -> None:
        # (Implementation unchanged)
        log.debug("TUI received state update", num_repos=len(message.repo_states))
        try:
            table = self.query_one(DataTable)
            current_keys = set(table.rows.keys())
            incoming_keys = set(message.repo_states.keys())
            for key_to_remove in current_keys - incoming_keys:
                 if table.is_valid_row_key(key_to_remove): table.remove_row(key_to_remove)
            for repo_id_obj, state in message.repo_states.items():
                repo_id = str(repo_id_obj)
                last_change_str = state.last_change_time.strftime("%H:%M:%S") if state.last_change_time else "--:--:--"
                error_str = state.error_message or ""
                status_style, status_icon = self._get_status_style_and_icon(state.status)
                display_error = error_str[:60] + ("..." if len(error_str) > 60 else "")
                row_data = (f"[{status_style}]{status_icon} {state.status.name}[/]", last_change_str, str(state.save_count), display_error)
                if table.is_valid_row_key(repo_id): table.update_row(repo_id, *row_data, update_width=False)
                else: table.add_row(repo_id, *row_data, key=repo_id)
        except Exception as e: log.error("Failed to update TUI table", error=str(e))

    def on_log_message_update(self, message: LogMessageUpdate) -> None:
         # (Implementation unchanged)
         try:
             log_widget = self.query_one(TextualLog)
             prefix = f"[dim]({message.repo_id or 'SYSTEM'})[/dim] "
             level_style = self._get_level_style(message.level)
             level_prefix = f"[{level_style}]{message.level.upper():<8}[/]"
             log_widget.write_line(f"{level_prefix} {prefix}{message.message}")
         except Exception as e: log.error("Failed to write to TUI log", error=str(e))

    # --- Helper Methods (remain the same) ---
    def _update_sub_title(self, text: str) -> None:
        try: self.sub_title = text
        except Exception as e: log.warning("Failed to update TUI sub-title", error=str(e))
    def _get_status_style_and_icon(self, status: RepositoryStatus) -> tuple[str, str]:
        # (Implementation unchanged)
        match status:
            case RepositoryStatus.IDLE: return ("dim", "✅")
            case RepositoryStatus.CHANGED: return ("yellow", "📝")
            case RepositoryStatus.TRIGGERED: return ("blue", "⏳")
            case RepositoryStatus.PROCESSING: return ("cyan", "⚙️")
            case RepositoryStatus.STAGING: return ("magenta", "➕")
            case RepositoryStatus.COMMITTING: return ("green", "💾")
            case RepositoryStatus.PUSHING: return ("bright_blue", "🚀")
            case RepositoryStatus.ERROR: return ("bold red", "❌")
            case _: return ("", "❓")
    def _get_level_style(self, level_name: str) -> str:
         # (Implementation unchanged)
         level = level_name.upper()
         if level == "CRITICAL": return "bold white on red"
         if level == "ERROR": return "bold red"
         if level == "WARNING": return "yellow"
         if level == "INFO": return "green"
         if level == "DEBUG": return "dim blue"
         if level == "SUCCESS": return "bold green"
         return "white"

# if __name__ == "__main__": remains the same
if __name__ == "__main__":
    try:
        test_config = Path(__file__).parent.parent.parent.parent / "examples" / "supsrc.conf"
        if not test_config.is_file(): pass
        else:
            dummy_shutdown = asyncio.Event()
            app_instance = SupsrcTuiApp(config_path=test_config, cli_shutdown_event=dummy_shutdown)
            app_instance.run()
    except NameError: pass
    except ImportError: pass

# 🔼⚙️

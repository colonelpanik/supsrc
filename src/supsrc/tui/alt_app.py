#
# supsrc/tui/alt_app.py
#
# this is an unfinish poc alternative tui app to explore

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.reactive import var
from textual.timer import Timer
from textual.widgets import (
    DataTable, Footer, Header, Label, ListItem, ListView, 
    Log as TextualLog, ProgressBar, Static, TabbedContent, TabPane
)
from textual.worker import Worker

import structlog

from supsrc.runtime.orchestrator import RepositoryStatesMap, WatchOrchestrator
from supsrc.state import RepositoryState, RepositoryStatus

if TYPE_CHECKING:
    Var = var
else:
    Var = object

log = structlog.get_logger("tui.alt_app")

# Enhanced Messages
class StateUpdate(Message):
    ALLOW_BUBBLE = True
    def __init__(self, repo_states: RepositoryStatesMap) -> None:
        self.repo_states = repo_states
        super().__init__()

class LogMessageUpdate(Message):
    ALLOW_BUBBLE = True
    def __init__(self, repo_id: str | None, level: str, message: str, timestamp: str = None) -> None:
        self.repo_id = repo_id
        self.level = level
        self.message = message
        self.timestamp = timestamp or ""
        super().__init__()

class RepositorySelected(Message):
    ALLOW_BUBBLE = True
    def __init__(self, repo_id: str | None) -> None:
        self.repo_id = repo_id
        super().__init__()

# Custom Widgets
class RepositoryListWidget(ListView):
    """Enhanced repository list with status indicators"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.repo_states: dict[str, RepositoryState] = {}
        
    def update_repositories(self, repo_states: RepositoryStatesMap) -> None:
        """Update repository list with current states"""
        self.repo_states = dict(repo_states)
        self.clear()
        
        for repo_id, state in repo_states.items():
            status_icon, status_style = self._get_status_display(state.status)
            save_count = f"💾 {state.save_count}"
            last_change = state.last_change_time.strftime("%H:%M:%S") if state.last_change_time else "--:--:--"
            
            # Create rich text for the list item
            repo_text = Text()
            repo_text.append(f"{status_icon} ", style=status_style)
            repo_text.append(f"{repo_id}", style="bold")
            repo_text.append(f" {save_count} ", style="dim")
            repo_text.append(f"⏱️ {last_change}", style="cyan")
            
            list_item = ListItem(Label(repo_text))
            list_item.repo_id = repo_id  # Store repo_id for selection
            self.append(list_item)
    
    def _get_status_display(self, status: RepositoryStatus) -> tuple[str, str]:
        """Get emoji and style for repository status"""
        status_map = {
            RepositoryStatus.IDLE: ("✅", "green"),
            RepositoryStatus.CHANGED: ("🔄", "yellow"),
            RepositoryStatus.TRIGGERED: ("⏳", "blue"),
            RepositoryStatus.PROCESSING: ("⚙️", "cyan"),
            RepositoryStatus.STAGING: ("📥", "magenta"),
            RepositoryStatus.COMMITTING: ("💾", "bright_green"),
            RepositoryStatus.PUSHING: ("🚀", "bright_blue"),
            RepositoryStatus.ERROR: ("❌", "red"),
        }
        return status_map.get(status, ("❓", "dim"))
    
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle repository selection"""
        if hasattr(event.item, 'repo_id'):
            self.post_message(RepositorySelected(event.item.repo_id))

class RepositoryDetailWidget(Static):
    """Detailed view of selected repository"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.selected_repo: str | None = None
        self.repo_state: RepositoryState | None = None
    
    def compose(self) -> ComposeResult:
        with TabbedContent():
            with TabPane("Overview", id="overview"):
                yield Static("", id="repo-overview")
            with TabPane("Activity", id="activity"):
                yield Static("", id="repo-activity")
            with TabPane("Files", id="files"):
                yield Static("", id="repo-files")
            with TabPane("Config", id="config"):
                yield Static("", id="repo-config")
    
    def update_repository(self, repo_id: str, repo_state: RepositoryState) -> None:
        """Update the detail view for selected repository"""
        self.selected_repo = repo_id
        self.repo_state = repo_state
        
        # Update overview tab
        overview_widget = self.query_one("#repo-overview", Static)
        overview_content = self._generate_overview_content(repo_id, repo_state)
        overview_widget.update(overview_content)
    
    def _generate_overview_content(self, repo_id: str, state: RepositoryState) -> str:
        """Generate overview content for repository"""
        status_icon, _ = self._get_status_display(state.status)
        last_change = state.last_change_time.strftime("%Y-%m-%d %H:%M:%S") if state.last_change_time else "Never"
        
        content = f"""[bold]🏠 {repo_id}[/bold]

[bold]Status:[/bold] {status_icon} {state.status.name}
[bold]Save Count:[/bold] 💾 {state.save_count}
[bold]Last Change:[/bold] ⏱️ {last_change}
[bold]Error:[/bold] {state.error_message or "None"}

[bold]Actions:[/bold]
• [link]Force Commit[/link]
• [link]Reset State[/link]
• [link]Configure Rules[/link]
"""
        return content
    
    def _get_status_display(self, status: RepositoryStatus) -> tuple[str, str]:
        """Get emoji and style for repository status"""
        status_map = {
            RepositoryStatus.IDLE: ("✅", "green"),
            RepositoryStatus.CHANGED: ("🔄", "yellow"),
            RepositoryStatus.TRIGGERED: ("⏳", "blue"),
            RepositoryStatus.PROCESSING: ("⚙️", "cyan"),
            RepositoryStatus.STAGING: ("📥", "magenta"),
            RepositoryStatus.COMMITTING: ("💾", "bright_green"),
            RepositoryStatus.PUSHING: ("🚀", "bright_blue"),
            RepositoryStatus.ERROR: ("❌", "red"),
        }
        return status_map.get(status, ("❓", "dim"))

class GlobalDashboardWidget(Static):
    """Global dashboard showing metrics and system status"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.repo_states: dict[str, RepositoryState] = {}
    
    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(classes="dashboard-section"):
                yield Label("[bold]📊 Metrics[/bold]")
                yield Static("", id="metrics-content")
            with Vertical(classes="dashboard-section"):
                yield Label("[bold]⚡ Activity[/bold]")
                yield Static("", id="activity-content")
            with Vertical(classes="dashboard-section"):
                yield Label("[bold]🔧 System[/bold]")
                yield Static("", id="system-content")
    
    def update_dashboard(self, repo_states: RepositoryStatesMap) -> None:
        """Update dashboard with current repository states"""
        self.repo_states = dict(repo_states)
        
        # Update metrics
        metrics_widget = self.query_one("#metrics-content", Static)
        metrics_content = self._generate_metrics_content()
        metrics_widget.update(metrics_content)
        
        # Update activity
        activity_widget = self.query_one("#activity-content", Static)
        activity_content = self._generate_activity_content()
        activity_widget.update(activity_content)
        
        # Update system
        system_widget = self.query_one("#system-content", Static)
        system_content = self._generate_system_content()
        system_widget.update(system_content)
    
    def _generate_metrics_content(self) -> str:
        """Generate metrics content"""
        total_repos = len(self.repo_states)
        active_repos = sum(1 for state in self.repo_states.values() 
                          if state.status != RepositoryStatus.IDLE)
        error_repos = sum(1 for state in self.repo_states.values() 
                         if state.status == RepositoryStatus.ERROR)
        total_saves = sum(state.save_count for state in self.repo_states.values())
        
        return f"""Total: {total_repos}
Active: {active_repos}
Errors: {error_repos}
Saves: {total_saves}"""
    
    def _generate_activity_content(self) -> str:
        """Generate activity content"""
        recent_changes = sum(1 for state in self.repo_states.values() 
                           if state.last_change_time and 
                           (state.status in [RepositoryStatus.CHANGED, RepositoryStatus.PROCESSING]))
        
        return f"""Recent: {recent_changes}
Processing: {sum(1 for state in self.repo_states.values() if state.status == RepositoryStatus.PROCESSING)}
Idle: {sum(1 for state in self.repo_states.values() if state.status == RepositoryStatus.IDLE)}"""
    
    def _generate_system_content(self) -> str:
        """Generate system content"""
        return """Status: 🟢 OK
Memory: Normal
Network: Connected"""

class EnhancedLogWidget(TextualLog):
    """Enhanced log widget with better formatting and filtering"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.max_lines = 1000
        self.auto_scroll = True
    
    def write_enhanced_log(self, repo_id: str | None, level: str, message: str, timestamp: str = "") -> None:
        """Write a formatted log message"""
        # Create timestamp if not provided
        if not timestamp:
            from datetime import datetime
            timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Get level styling
        level_style = self._get_level_style(level)
        level_prefix = f"[{level_style}]{level.upper():<8}[/]"
        
        # Format repo prefix
        repo_prefix = f"[dim]({repo_id or 'SYSTEM'})[/dim]"
        
        # Combine message
        full_message = f"{timestamp} {level_prefix} {repo_prefix} {message}"
        
        self.write_line(full_message)
        
        if self.auto_scroll:
            self.scroll_end()
    
    def _get_level_style(self, level_name: str) -> str:
        """Get style for log level"""
        styles = {
            "CRITICAL": "bold white on red",
            "ERROR": "bold red",
            "WARNING": "yellow",
            "INFO": "green",
            "DEBUG": "dim blue",
            "SUCCESS": "bold green",
        }
        return styles.get(level_name.upper(), "white")

class SupsrcEnhancedTuiApp(App):
    """Enhanced Supsrc TUI Application with split view design"""
    
    TITLE = "Supsrc Watcher"
    SUB_TITLE = "Enhanced Monitoring..."
    BINDINGS = [
        ("d", "toggle_dark", "Toggle Dark Mode"),
        ("q", "quit", "Quit Application"),
        ("ctrl+l", "clear_log", "Clear Log"),
        ("r", "refresh", "Refresh"),
        ("space", "toggle_pause", "Pause/Resume"),
    ]
    
    CSS = """
    Screen {
        layout: vertical;
    }
    
    #main-container {
        height: 1fr;
        layout: horizontal;
    }
    
    #left-panel {
        width: 50%;
        layout: vertical;
    }
    
    #right-panel {
        width: 50%;
        layout: vertical;
    }
    
    #repo-list {
        height: 60%;
        border: thick $accent;
        border-title: "Repositories";
    }
    
    #dashboard {
        height: 40%;
        border: thick $accent;
        border-title: "Dashboard";
    }
    
    #repo-details {
        height: 70%;
        border: thick $accent;
        border-title: "Repository Details";
    }
    
    #activity-log {
        height: 30%;
        border: thick $accent;
        border-title: "Activity Log";
    }
    
    .dashboard-section {
        width: 1fr;
        height: 1fr;
        margin: 1;
        padding: 1;
        border: round $primary;
    }
    
    ListView > .list--option {
        padding: 0 1;
    }
    
    ListView > .list--option-highlighted {
        background: $accent;
    }
    """
    
    if TYPE_CHECKING:
        repo_states_data: Var[dict[str, Any]]
        selected_repo: Var[str | None]
    
    repo_states_data = var({})
    selected_repo = var(None)
    
    def __init__(self, config_path: Path, cli_shutdown_event: asyncio.Event, **kwargs):
        super().__init__(**kwargs)
        self._config_path = config_path
        self._orchestrator: WatchOrchestrator | None = None
        self._shutdown_event = asyncio.Event()
        self._cli_shutdown_event = cli_shutdown_event
        self._worker: Worker | None = None
        self._shutdown_check_timer: Timer | None = None
        self._paused = False
    
    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="main-container"):
            with Vertical(id="left-panel"):
                yield RepositoryListWidget(id="repo-list")
                yield GlobalDashboardWidget(id="dashboard")
            with Vertical(id="right-panel"):
                yield RepositoryDetailWidget(id="repo-details")
                yield EnhancedLogWidget(id="activity-log", highlight=True, max_lines=1000)
        yield Footer()
    
    def on_mount(self) -> None:
        """Initialize the application"""
        log.info("Enhanced TUI mounted. Initializing components.")
        self._update_sub_title("Initializing...")
        
        # Start orchestrator worker
        self._worker = self.run_worker(self._run_orchestrator, thread=True, group="orchestrator")
        
        # Start shutdown check timer
        self._shutdown_check_timer = self.set_interval(0.5, self._check_external_shutdown, name="ExternalShutdownCheck")
        
        self._update_sub_title("Monitoring...")
    
    async def _run_orchestrator(self) -> None:
        """Run the orchestrator in a worker thread"""
        log.info("Orchestrator worker started.")
        self._orchestrator = WatchOrchestrator(self._config_path, self._shutdown_event, app=self)
        try:
            await self._orchestrator.run()
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
    
    async def _check_external_shutdown(self) -> None:
        """Check for external shutdown signal"""
        if self._cli_shutdown_event.is_set() and not self._shutdown_event.is_set():
            log.warning("External shutdown detected (CLI signal), stopping TUI and orchestrator.")
            self._update_sub_title("Shutdown requested...")
            await self.action_quit()
    
    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Handle worker state changes"""
        log.debug(f"Worker {event.worker.name!r} state changed to {event.state!r}")
        if event.worker == self._worker and event.state in ("SUCCESS", "ERROR"):
            log.info(f"Orchestrator worker stopped with state: {event.state!r}")
            if not self._shutdown_event.is_set() and not self._cli_shutdown_event.is_set():
                self.call_later(self.action_quit)
    
    # Action Methods
    def action_toggle_dark(self) -> None:
        """Toggle dark mode"""
        try:
            self.dark = not self.dark
        except Exception as e:
            log.error("Failed to toggle dark mode", error=str(e))
    
    def action_clear_log(self) -> None:
        """Clear the activity log"""
        try:
            log_widget = self.query_one("#activity-log", EnhancedLogWidget)
            log_widget.clear()
            log_widget.write_enhanced_log(None, "INFO", "Log cleared.")
        except Exception as e:
            log.error("Failed to clear TUI log", error=str(e))
    
    def action_refresh(self) -> None:
        """Force refresh of all components"""
        try:
            # Force update of all widgets with current data
            if self.repo_states_data:
                self.post_message(StateUpdate(self.repo_states_data))
            log_widget = self.query_one("#activity-log", EnhancedLogWidget)
            log_widget.write_enhanced_log(None, "INFO", "Display refreshed.")
        except Exception as e:
            log.error("Failed to refresh display", error=str(e))
    
    def action_toggle_pause(self) -> None:
        """Toggle pause/resume monitoring"""
        self._paused = not self._paused
        status = "paused" if self._paused else "resumed"
        self._update_sub_title(f"Monitoring {status}")
        
        log_widget = self.query_one("#activity-log", EnhancedLogWidget)
        log_widget.write_enhanced_log(None, "INFO", f"Monitoring {status}.")
    
    async def action_quit(self) -> None:
        """Quit the application"""
        log.info("Quit action triggered.")
        self._update_sub_title("Quitting...")
        
        if not self._shutdown_event.is_set():
            self._shutdown_event.set()
        
        # Stop timer
        if self._shutdown_check_timer:
            try:
                self._shutdown_check_timer.stop()
                log.debug("Stopped external shutdown check timer.")
            except Exception as e:
                log.error("Error stopping shutdown check timer", error=str(e))
        
        await asyncio.sleep(0.3)
        
        # Cancel worker
        if self._worker and self._worker.is_running:
            log.info("Attempting to cancel orchestrator worker...")
            try:
                await self._worker.cancel()
            except Exception:
                log.exception("Error during worker cancel")
        
        log.info("Exiting Enhanced TUI application.")
        self.exit(0)
    
    # Message Handlers
    def on_state_update(self, message: StateUpdate) -> None:
        """Handle repository state updates"""
        log.debug("TUI received state update", num_repos=len(message.repo_states))
        try:
            # Update repository list
            repo_list = self.query_one("#repo-list", RepositoryListWidget)
            repo_list.update_repositories(message.repo_states)
            
            # Update dashboard
            dashboard = self.query_one("#dashboard", GlobalDashboardWidget)
            dashboard.update_dashboard(message.repo_states)
            
            # Update details if a repo is selected
            if self.selected_repo and self.selected_repo in message.repo_states:
                detail_widget = self.query_one("#repo-details", RepositoryDetailWidget)
                detail_widget.update_repository(self.selected_repo, message.repo_states[self.selected_repo])
            
            # Store current states
            self.repo_states_data = dict(message.repo_states)
            
        except Exception as e:
            log.error("Failed to update TUI state", error=str(e))
    
    def on_log_message_update(self, message: LogMessageUpdate) -> None:
        """Handle log message updates"""
        try:
            log_widget = self.query_one("#activity-log", EnhancedLogWidget)
            log_widget.write_enhanced_log(message.repo_id, message.level, message.message, message.timestamp)
        except Exception as e:
            log.error("Failed to write to TUI log", error=str(e))
    
    @on(RepositorySelected)
    def on_repository_selected(self, message: RepositorySelected) -> None:
        """Handle repository selection"""
        try:
            self.selected_repo = message.repo_id
            
            if message.repo_id and message.repo_id in self.repo_states_data:
                detail_widget = self.query_one("#repo-details", RepositoryDetailWidget)
                detail_widget.update_repository(message.repo_id, self.repo_states_data[message.repo_id])
                
                log_widget = self.query_one("#activity-log", EnhancedLogWidget)
                log_widget.write_enhanced_log(None, "INFO", f"Selected repository: {message.repo_id}")
            
        except Exception as e:
            log.error("Failed to handle repository selection", error=str(e))
    
    def _update_sub_title(self, text: str) -> None:
        """Update the application sub-title"""
        try:
            self.sub_title = text
        except Exception as e:
            log.warning("Failed to update TUI sub-title", error=str(e))

# For backward compatibility
SupsrcTuiApp = SupsrcEnhancedTuiApp
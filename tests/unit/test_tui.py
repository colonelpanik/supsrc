#
# tests/unit/test_tui.py
#
"""
Comprehensive tests for the TUI application.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from supsrc.state import RepositoryState
from supsrc.tui.app import LogMessageUpdate, StateUpdate, SupsrcTuiApp, TimerManager


@pytest.fixture
def mock_config_path() -> Path:
    """Mock configuration path for TUI testing."""
    return Path("/mock/config.conf")


@pytest.fixture
def mock_shutdown_event() -> asyncio.Event:
    """Mock shutdown event for TUI testing."""
    return asyncio.Event()


class TestTimerManager:
    """Test the TimerManager functionality."""

    def test_timer_creation(self) -> None:
        """Test timer creation and tracking."""
        mock_app = Mock()
        mock_timer = Mock()
        mock_app.set_interval.return_value = mock_timer

        manager = TimerManager(mock_app)

        # Create timer
        callback = Mock()
        timer = manager.create_timer("test_timer", 1.0, callback)

        assert timer == mock_timer
        assert "test_timer" in manager._timers
        mock_app.set_interval.assert_called_once_with(1.0, callback, name="test_timer")

    def test_timer_replacement(self) -> None:
        """Test replacing an existing timer."""
        mock_app = Mock()
        old_timer = Mock()
        new_timer = Mock()
        mock_app.set_interval.side_effect = [old_timer, new_timer]

        manager = TimerManager(mock_app)

        # Create first timer
        callback = Mock()
        manager.create_timer("test_timer", 1.0, callback)

        # Replace with new timer
        manager.create_timer("test_timer", 2.0, callback)

        # Old timer should be stopped
        old_timer.stop.assert_called_once()
        assert manager._timers["test_timer"] == new_timer

    def test_stop_timer(self) -> None:
        """Test stopping a specific timer."""
        mock_app = Mock()
        mock_timer = Mock()
        mock_timer.is_cancelled.return_value = False
        mock_app.set_interval.return_value = mock_timer

        manager = TimerManager(mock_app)

        # Create and stop timer
        manager.create_timer("test_timer", 1.0, Mock())
        result = manager.stop_timer("test_timer")

        assert result is True
        mock_timer.stop.assert_called_once()
        assert "test_timer" not in manager._timers

    def test_stop_nonexistent_timer(self) -> None:
        """Test stopping a timer that doesn't exist."""
        mock_app = Mock()
        manager = TimerManager(mock_app)

        result = manager.result = manager.stop_timer("nonexistent")

        assert result is False

    def test_stop_all_timers(self) -> None:
        """Test stopping all timers."""
        mock_app = Mock()
        timer1 = Mock()
        timer2 = Mock()
        timer1.is_cancelled.return_value = False
        timer2.is_cancelled.return_value = False
        mock_app.set_interval.side_effect = [timer1, timer2]

        manager = TimerManager(mock_app)

        # Create multiple timers
        manager.create_timer("timer1", 1.0, Mock())
        manager.create_timer("timer2", 2.0, Mock())

        # Stop all
        manager.stop_all_timers()

        timer1.stop.assert_called_once()
        timer2.stop.assert_called_once()
        assert len(manager._timers) == 0


class TestSupsrcTuiApp:
    """Test the main TUI application."""

    @pytest.fixture
    def tui_app(
        self,
        mock_config_path: Path,
        mock_shutdown_event: asyncio.Event
    ) -> SupsrcTuiApp:
        """Create a TUI app instance for testing."""
        return SupsrcTuiApp(mock_config_path, mock_shutdown_event)

    def test_app_initialization(self, tui_app: SupsrcTuiApp) -> None:
        """Test TUI app initialization."""
        assert tui_app._config_path == Path("/mock/config.conf")
        assert tui_app._orchestrator is None
        assert tui_app._worker is None
        assert isinstance(tui_app._timer_manager, TimerManager)
        assert tui_app._is_shutting_down is False

    def test_reactive_variables(self, tui_app: SupsrcTuiApp) -> None:
        """Test reactive variable initialization."""
        assert tui_app.repo_states_data == {}
        assert tui_app.show_detail_pane is False
        assert tui_app.selected_repo_id is None

    @patch("supsrc.tui.app.DataTable")
    @patch("supsrc.tui.app.TextualLog")
    def test_compose_method(
        self,
        mock_log: Mock,
        mock_table: Mock,
        tui_app: SupsrcTuiApp
    ) -> None:
        """Test the compose method creates proper widget structure."""
        # Mock the widget creation
        mock_table.return_value = Mock()
        mock_log.return_value = Mock()

        # This would normally be called by Textual framework
        widgets = list(tui_app.compose())

        # Should create header, containers, and footer
        assert len(widgets) >= 2  # At least Header and Footer

    def test_watch_show_detail_pane(self, tui_app: SupsrcTuiApp) -> None:
        """Test detail pane visibility watcher."""
        # Mock the query methods
        tui_app.query_one = Mock()
        mock_container = Mock()
        mock_container.styles = Mock()
        tui_app.query_one.return_value = mock_container

        # Test showing detail pane
        tui_app.watch_show_detail_pane(True)

        # Should have been called multiple times for different containers
        assert tui_app.query_one.call_count >= 3

    def test_action_toggle_dark(self, tui_app: SupsrcTuiApp) -> None:
        """Test dark mode toggle action."""
        # Mock screen
        tui_app.screen = Mock()
        tui_app.screen.dark = False

        tui_app.action_toggle_dark()

        assert tui_app.screen.dark is True

    def test_action_clear_log(self, tui_app: SupsrcTuiApp) -> None:
        """Test log clearing action."""
        # Mock log widget and post_message
        mock_log = Mock()
        tui_app.query_one = Mock(return_value=mock_log)
        tui_app.post_message = Mock()

        tui_app.action_clear_log()

        mock_log.clear.assert_called_once()
        tui_app.post_message.assert_called_once()

    async def test_action_quit(self, tui_app: SupsrcTuiApp) -> None:
        """Test quit action."""
        # Mock dependencies
        tui_app._timer_manager.stop_all_timers = Mock()
        tui_app.exit = Mock()

        await tui_app.action_quit()

        assert tui_app._is_shutting_down is True
        assert tui_app._shutdown_event.is_set()
        tui_app._timer_manager.stop_all_timers.assert_called_once()
        tui_app.exit.assert_called_once_with(0)

    def test_on_state_update(self, tui_app: SupsrcTuiApp) -> None:
        """Test state update message handling."""
        # Mock table
        mock_table = Mock()
        mock_table.rows.keys.return_value = set()
        mock_table.is_valid_row_key.return_value = False
        tui_app.query_one = Mock(return_value=mock_table)

        # Create test state
        test_state = RepositoryState(repo_id="test-repo")
        test_state.display_status_emoji = "✅"
        test_state.last_change_time = None
        test_state.rule_emoji = "⏳"
        test_state.rule_dynamic_indicator = "Waiting"
        test_state.action_description = None
        test_state.last_commit_short_hash = "abc123"
        test_state.last_commit_message_summary = "Test commit"

        message = StateUpdate({"test-repo": test_state})

        tui_app.on_state_update(message)

        # Should add row for new repository
        mock_table.add_row.assert_called_once()

    def test_on_log_message_update(self, tui_app: SupsrcTuiApp) -> None:
        """Test log message update handling."""
        # Mock log widget
        mock_log = Mock()
        tui_app.query_one = Mock(return_value=mock_log)

        message = LogMessageUpdate("test-repo", "INFO", "Test message")

        tui_app.on_log_message_update(message)

        mock_log.write_line.assert_called_once()
        # Verify the formatted message contains repo ID and level
        call_args = mock_log.write_line.call_args[0][0]
        assert "test-repo" in call_args
        assert "INFO" in call_args
        assert "Test message" in call_args

    def test_get_level_style(self, tui_app: SupsrcTuiApp) -> None:
        """Test log level styling."""
        assert tui_app._get_level_style("CRITICAL") == "bold white on red"
        assert tui_app._get_level_style("ERROR") == "bold red"
        assert tui_app._get_level_style("WARNING") == "yellow"
        assert tui_app._get_level_style("INFO") == "green"
        assert tui_app._get_level_style("DEBUG") == "dim blue"
        assert tui_app._get_level_style("UNKNOWN") == "white"


class TestTuiIntegration:
    """Test TUI integration scenarios."""

    @pytest.fixture
    def mock_orchestrator(self) -> Mock:
        """Create a mock orchestrator for TUI testing."""
        orchestrator = Mock()
        orchestrator.get_repository_details = AsyncMock()
        return orchestrator

    async def test_repo_detail_fetching(
        self,
        mock_config_path: Path,
        mock_shutdown_event: asyncio.Event,
        mock_orchestrator: Mock
    ) -> None:
        """Test repository detail fetching workflow."""
        tui_app = SupsrcTuiApp(mock_config_path, mock_shutdown_event)
        tui_app._orchestrator = mock_orchestrator

        # Mock detail response
        mock_orchestrator.get_repository_details.return_value = {
            "commit_history": ["abc123 - Test commit", "def456 - Another commit"]
        }

        # Mock TUI components
        tui_app.query_one = Mock()
        mock_detail_log = Mock()
        tui_app.query_one.return_value = mock_detail_log
        tui_app.post_message = Mock()

        # Fetch details
        await tui_app._fetch_repo_details_worker("test-repo")

        # Verify orchestrator was called
        mock_orchestrator.get_repository_details.assert_called_once_with("test-repo")

        # Verify message was posted
        tui_app.post_message.assert_called_once()
        posted_message = tui_app.post_message.call_args[0][0]
        assert hasattr(posted_message, "repo_id")
        assert posted_message.repo_id == "test-repo"

    async def test_repo_detail_error_handling(
        self,
        mock_config_path: Path,
        mock_shutdown_event: asyncio.Event,
        mock_orchestrator: Mock
    ) -> None:
        """Test error handling in repository detail fetching."""
        tui_app = SupsrcTuiApp(mock_config_path, mock_shutdown_event)
        tui_app._orchestrator = mock_orchestrator

        # Mock orchestrator to raise error
        mock_orchestrator.get_repository_details.side_effect = Exception("Test error")

        # Mock TUI components
        tui_app.post_message = Mock()

        # Fetch details (should handle error gracefully)
        await tui_app._fetch_repo_details_worker("test-repo")

        # Should post error message
        tui_app.post_message.assert_called_once()
        posted_message = tui_app.post_message.call_args[0][0]
        assert "Error loading details" in str(posted_message.details)

    def test_action_select_repo_for_detail(
        self,
        mock_config_path: Path,
        mock_shutdown_event: asyncio.Event
    ) -> None:
        """Test repository selection for detail view."""
        tui_app = SupsrcTuiApp(mock_config_path, mock_shutdown_event)
        tui_app._orchestrator = Mock()

        # Mock table with selected row
        mock_table = Mock()
        mock_table.cursor_row = 0
        mock_table.get_row_key.return_value = "test-repo"

        # Mock detail log
        mock_detail_log = Mock()

        # Mock query_one to return appropriate widgets
        def mock_query_one(selector, widget_type=None):
            if "repo-table" in str(selector) or selector == DataTable:
                return mock_table
            elif "repo_detail_log" in str(selector):
                return mock_detail_log
            return Mock()

        tui_app.query_one = mock_query_one
        tui_app.run_worker = Mock()

        # Execute action
        tui_app.action_select_repo_for_detail()

        # Verify state changes
        assert tui_app.selected_repo_id == "test-repo"
        assert tui_app.show_detail_pane is True

        # Verify worker was started
        tui_app.run_worker.assert_called_once()

    def test_action_hide_detail_pane(
        self,
        mock_config_path: Path,
        mock_shutdown_event: asyncio.Event
    ) -> None:
        """Test hiding the detail pane."""
        tui_app = SupsrcTuiApp(mock_config_path, mock_shutdown_event)

        # Set up initial state
        tui_app.show_detail_pane = True
        tui_app.selected_repo_id = "test-repo"

        # Mock widgets
        mock_detail_log = Mock()
        mock_table = Mock()

        def mock_query_one(selector, widget_type=None):
            if "repo_detail_log" in str(selector):
                return mock_detail_log
            elif selector == DataTable:
                return mock_table
            return Mock()

        tui_app.query_one = mock_query_one

        # Execute action
        tui_app.action_hide_detail_pane()

        # Verify state changes
        assert tui_app.show_detail_pane is False
        assert tui_app.selected_repo_id is None

        # Verify cleanup
        mock_detail_log.clear.assert_called_once()
        mock_table.focus.assert_called_once()


class TestTuiErrorHandling:
    """Test TUI error handling and resilience."""

    def test_widget_query_error_handling(
        self,
        mock_config_path: Path,
        mock_shutdown_event: asyncio.Event
    ) -> None:
        """Test handling of widget query errors."""
        tui_app = SupsrcTuiApp(mock_config_path, mock_shutdown_event)

        # Mock query_one to raise error
        tui_app.query_one = Mock(side_effect=Exception("Widget not found"))

        # Actions should handle errors gracefully
        tui_app.action_clear_log()  # Should not raise
        tui_app.action_hide_detail_pane()  # Should not raise

        # App should still be functional
        assert not tui_app._is_shutting_down

    def test_orchestrator_crash_handling(
        self,
        mock_config_path: Path,
        mock_shutdown_event: asyncio.Event
    ) -> None:
        """Test handling of orchestrator crashes."""
        tui_app = SupsrcTuiApp(mock_config_path, mock_shutdown_event)

        # Mock the call_later and post_message methods
        tui_app.call_later = Mock()

        # Create a mock worker that represents a crashed orchestrator
        mock_worker = Mock()
        mock_worker.name = "orchestrator"
        mock_worker.is_running = False

        # Create state changed event for error
        from textual.worker import Worker
        state_event = Worker.StateChanged(mock_worker, "ERROR")

        tui_app._worker = mock_worker

        # Handle the event
        tui_app.on_worker_state_changed(state_event)

        # Should trigger quit action
        tui_app.call_later.assert_called_once()

    async def test_external_shutdown_handling(
        self,
        mock_config_path: Path,
        mock_shutdown_event: asyncio.Event
    ) -> None:
        """Test handling of external shutdown signals."""
        tui_app = SupsrcTuiApp(mock_config_path, mock_shutdown_event)
        tui_app.action_quit = AsyncMock()

        # Signal external shutdown
        mock_shutdown_event.set()

        # Check external shutdown
        await tui_app._check_external_shutdown()

        # Should trigger quit action
        tui_app.action_quit.assert_called_once()

    def test_timer_manager_error_recovery(self) -> None:
        """Test timer manager error recovery."""
        mock_app = Mock()
        mock_timer = Mock()
        mock_timer.stop.side_effect = Exception("Timer error")
        mock_timer.is_cancelled.return_value = False
        mock_app.set_interval.return_value = mock_timer

        manager = TimerManager(mock_app)

        # Create timer
        manager.create_timer("test_timer", 1.0, Mock())

        # Stop timer (should handle error gracefully)
        result = manager.stop_timer("test_timer")

        # Should return False but not crash
        assert result is False
        # Timer should still be removed from tracking
        assert "test_timer" not in manager._timers


class TestTuiAccessibility:
    """Test TUI accessibility and usability features."""

    def test_keyboard_bindings(
        self,
        mock_config_path: Path,
        mock_shutdown_event: asyncio.Event
    ) -> None:
        """Test that all keyboard bindings are properly defined."""
        tui_app = SupsrcTuiApp(mock_config_path, mock_shutdown_event)

        # Verify bindings exist
        bindings = {binding.key: binding.action for binding in tui_app.BINDINGS}

        expected_bindings = {
            "d": "toggle_dark",
            "q": "quit",
            "ctrl+l": "clear_log",
            "enter": "select_repo_for_detail",
            "escape": "hide_detail_pane",
            "r": "refresh_details",
        }

        for key, action in expected_bindings.items():
            assert key in bindings
            assert bindings[key] == action

    def test_widget_focus_management(
        self,
        mock_config_path: Path,
        mock_shutdown_event: asyncio.Event
    ) -> None:
        """Test proper focus management between widgets."""
        tui_app = SupsrcTuiApp(mock_config_path, mock_shutdown_event)

        # Mock widgets
        mock_table = Mock()
        tui_app.query_one = Mock(return_value=mock_table)

        # Test focus return after hiding detail pane
        tui_app.show_detail_pane = True
        tui_app.action_hide_detail_pane()

        # Should focus back to table
        mock_table.focus.assert_called_once()

    def test_progress_bar_rendering(
        self,
        mock_config_path: Path,
        mock_shutdown_event: asyncio.Event
    ) -> None:
        """Test progress bar rendering in action display."""
        tui_app = SupsrcTuiApp(mock_config_path, mock_shutdown_event)

        # Mock table
        mock_table = Mock()
        mock_table.rows.keys.return_value = set()
        mock_table.is_valid_row_key.return_value = False
        tui_app.query_one = Mock(return_value=mock_table)

        # Create state with progress information
        test_state = RepositoryState(repo_id="test-repo")
        test_state.action_description = "Processing"
        test_state.action_progress_total = 10
        test_state.action_progress_completed = 5
        test_state.display_status_emoji = "🔄"
        test_state.rule_emoji = "⏳"
        test_state.rule_dynamic_indicator = "Working"

        message = StateUpdate({"test-repo": test_state})

        tui_app.on_state_update(message)

        # Verify progress bar was included in action display
        call_args = mock_table.add_row.call_args[0]
        action_display = call_args[4]  # 5th column is action display

        assert "Processing" in action_display
        assert "50%" in action_display  # 5/10 = 50%
        assert "❚" in action_display  # Progress bar character

# 🧪💻

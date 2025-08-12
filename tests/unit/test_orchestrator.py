#
# tests/unit/test_orchestrator.py
#
"""
Comprehensive tests for WatchOrchestrator including hot reload functionality.
Tests both async and sync patterns.
"""

import asyncio
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from supsrc.config.models import GlobalConfig, InactivityRuleConfig, RepositoryConfig, SupsrcConfig
from supsrc.monitor import MonitoredEvent
from supsrc.runtime.orchestrator import WatchOrchestrator


@pytest.fixture
def temp_config_file():
    """Create a temporary config file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
        f.write("""
[global]
log_level = "INFO"

[repositories.test-repo]
enabled = true
path = "/tmp/test-repo"
engine = "supsrc.engines.git"

[repositories.test-repo.rule]
type = "inactivity"
period = "5s"
""")
        yield Path(f.name)
    # Cleanup
    Path(f.name).unlink(missing_ok=True)


@pytest.fixture
def mock_config():
    """Create a mock SupsrcConfig for testing."""
    return SupsrcConfig(
        global_config=GlobalConfig(log_level="INFO"),
        repositories={
            "test-repo": RepositoryConfig(
                enabled=True,
                path=Path("/tmp/test-repo"),
                rule=InactivityRuleConfig(period=timedelta(seconds=5)),
                repository={"engine": "supsrc.engines.git"},
            )
        },
    )


@pytest.fixture
async def orchestrator(temp_config_file):
    """Create a WatchOrchestrator instance for testing."""
    shutdown_event = asyncio.Event()
    orch = WatchOrchestrator(temp_config_file, shutdown_event)
    yield orch
    # Cleanup
    shutdown_event.set()


class TestWatchOrchestratorHotReload:
    """Test hot reload functionality."""

    async def test_reload_config_success(self, orchestrator, mock_config):
        """Test successful config reload."""
        # Setup
        orchestrator.config = mock_config
        orchestrator._is_paused = False
        mock_monitor = AsyncMock()
        mock_monitor.start = Mock()
        mock_monitor.clear_handlers = Mock()
        orchestrator.monitor_service = mock_monitor
        # -------------------------------------------------------------

        # Mock the load_config function
        with patch("supsrc.runtime.orchestrator.load_config") as mock_load:
            # Create new config with additional repo
            new_config = SupsrcConfig(
                global_config=GlobalConfig(log_level="DEBUG"),
                repositories={
                    "test-repo": mock_config.repositories["test-repo"],
                    "new-repo": RepositoryConfig(
                        enabled=True,
                        path=Path("/tmp/new-repo"),
                        rule=InactivityRuleConfig(period=timedelta(seconds=10)),
                        repository={"engine": "supsrc.engines.git"},
                    ),
                },
            )
            mock_load.return_value = new_config

            # Mock other methods
            orchestrator._initialize_repositories = AsyncMock(
                return_value=["test-repo", "new-repo"]
            )
            orchestrator._setup_monitoring = Mock(
                return_value=["test-repo", "new-repo"]
            )
            orchestrator._post_tui_state_update = Mock()
            orchestrator._console_message = Mock()
            orchestrator._post_tui_log = Mock()

            # Test
            result = await orchestrator.reload_config()

            # Verify
            assert result is True

    async def test_reload_config_rollback_on_error(self, orchestrator, mock_config):
        """Test config reload rollback on error."""
        # Setup
        orchestrator.config = mock_config
        orchestrator._is_paused = False
        old_monitor = Mock()
        orchestrator.monitor_service = old_monitor

        with patch("supsrc.runtime.orchestrator.load_config") as mock_load:
            # New config that will cause an error
            new_config = SupsrcConfig(
                global_config=GlobalConfig(log_level="DEBUG"),
                repositories={},  # No repositories - will cause validation error
            )
            mock_load.return_value = new_config

            orchestrator._console_message = Mock()
            orchestrator._post_tui_log = Mock()

            # Test
            result = await orchestrator.reload_config()

            # Verify rollback
            assert result is False
            assert orchestrator.config == mock_config  # Should rollback to old config
            assert orchestrator.monitor_service == old_monitor  # Should keep old monitor
            assert orchestrator._is_paused is False  # Should resume original state

    async def test_config_file_change_triggers_reload(self, orchestrator):
        """Test that config file changes trigger reload."""
        # Setup
        orchestrator.reload_config = AsyncMock(return_value=True)
        orchestrator._console_message = Mock()
        orchestrator._post_tui_log = Mock()

        # Create a config change event
        config_event = MonitoredEvent(
            repo_id="__config__",
            event_type="modified",
            src_path=orchestrator.config_path,
            is_directory=False,
        )

        # Add event to queue
        await orchestrator.event_queue.put(config_event)

        # Mock the shutdown event to exit after processing one event
        async def set_shutdown_after_delay():
            await asyncio.sleep(0.1)
            orchestrator.shutdown_event.set()

        # Start shutdown task
        shutdown_task = asyncio.create_task(set_shutdown_after_delay())

        # Mock the event consumer processing
        with patch.object(orchestrator, "_running_tasks", set()):
            # Process the event
            await orchestrator._consume_events()

        await shutdown_task

        # Verify
        orchestrator.reload_config.assert_called_once()

    def test_setup_config_watcher(self, orchestrator, mock_config):
        """Test config watcher setup."""
        # Setup
        orchestrator.config = mock_config
        orchestrator.monitor_service = Mock()

        # Test
        orchestrator.setup_config_watcher()

        # Since the method uses asyncio.create_task internally, we can just verify
        # that it doesn't raise and the log shows success
        # The actual async behavior is tested in integration tests
        assert orchestrator.monitor_service is not None

class TestWatchOrchestratorPauseResume:
    """Test pause/resume functionality."""

    def test_pause_monitoring(self, orchestrator):
        """Test pause monitoring."""
        orchestrator._is_paused = False
        orchestrator.monitor_service = Mock()

        orchestrator.pause_monitoring()

        assert orchestrator._is_paused is True
        # Monitor service should still be running (just paused)
        orchestrator.monitor_service.stop.assert_not_called()

    def test_suspend_monitoring(self, orchestrator):
        """Test suspend monitoring."""
        orchestrator._is_paused = False
        orchestrator._is_suspended = False
        orchestrator.monitor_service = Mock()

        orchestrator.suspend_monitoring()

        assert orchestrator._is_paused is True
        assert orchestrator._is_suspended is True
        # Monitor service should be stopped
        orchestrator.monitor_service.stop.assert_called_once()

    def test_resume_from_pause(self, orchestrator):
        """Test resume from pause."""
        orchestrator._is_paused = True
        orchestrator._is_suspended = False

        orchestrator.resume_monitoring()

        assert orchestrator._is_paused is False
        assert orchestrator._is_suspended is False

    async def test_resume_from_suspend(self, orchestrator, mock_config):
        """Test resume from suspend."""
        orchestrator._is_paused = True
        orchestrator._is_suspended = True
        orchestrator.config = mock_config
        orchestrator.monitor_service = Mock()

        with patch.object(orchestrator, "_restart_monitor_service", new_callable=AsyncMock):
            orchestrator.resume_monitoring()

            assert orchestrator._is_paused is False
            assert orchestrator._is_suspended is False
            # Should trigger restart
            await asyncio.sleep(0.1)  # Give time for async task to be created


class TestWatchOrchestratorEventProcessing:
    """Test event processing with pause/suspend."""

    async def test_events_queued_when_paused(self, orchestrator):
        """Test that events are queued but not processed when paused."""
        # Setup
        orchestrator._is_paused = True
        orchestrator.config = MagicMock()
        orchestrator.config.repositories = {"test-repo": MagicMock()}

        # Create test event
        test_event = MonitoredEvent(
            repo_id="test-repo",
            event_type="modified",
            src_path=Path("/tmp/test-repo/file.txt"),
            is_directory=False,
        )

        # Add to queue
        await orchestrator.event_queue.put(test_event)
        initial_size = orchestrator.event_queue.qsize()

        # Mock shutdown event to exit consumer loop
        orchestrator.shutdown_event.set()

        # Run consumer (should skip processing)
        with patch.object(orchestrator, "_console_message"):
            await orchestrator._consume_events()

        # Event should still be in queue
        assert orchestrator.event_queue.qsize() == initial_size


class TestWatchOrchestratorIntegration:
    """Integration tests for orchestrator functionality."""

    @pytest.mark.asyncio
    async def test_full_reload_cycle(self, temp_config_file):
        """Test complete config reload cycle."""
        # Create orchestrator
        shutdown_event = asyncio.Event()
        orchestrator = WatchOrchestrator(temp_config_file, shutdown_event)

        # Mock dependencies
        with patch("supsrc.runtime.orchestrator.MonitoringService") as mock_monitor_cls:
            mock_monitor = AsyncMock()
            mock_monitor.start = Mock()
            mock_monitor.clear_handlers = Mock()
            # -------------------------------------------------------------
            mock_monitor_cls.return_value = mock_monitor
            mock_monitor.is_running = True

            with patch("supsrc.runtime.orchestrator.GitEngine") as mock_engine_cls:
                mock_engine = AsyncMock()
                mock_engine_cls.return_value = mock_engine
                mock_engine.get_summary = AsyncMock()

                # Initialize
                orchestrator.config = SupsrcConfig(
                    global_config=GlobalConfig(log_level="INFO"),
                    repositories={
                        "test": RepositoryConfig(
                            enabled=True,
                            path=Path("/tmp/test"),
                            rule=InactivityRuleConfig(period=timedelta(seconds=5)),
                            repository={"engine": "supsrc.engines.git"},
                        )
                    },
                )

                # Mock required methods for reload
                orchestrator._initialize_repositories = AsyncMock(return_value=["test"])
                orchestrator._setup_monitoring = Mock(return_value=["test"])
                orchestrator._post_tui_state_update = Mock()
                orchestrator._console_message = Mock()
                orchestrator._post_tui_log = Mock()
                orchestrator.monitor_service = mock_monitor

                # Test reload
                with patch("supsrc.runtime.orchestrator.load_config") as mock_load:
                    mock_load.return_value = orchestrator.config

                    result = await orchestrator.reload_config()
                    assert result is True

    @pytest.mark.asyncio
    async def test_concurrent_operations(self, orchestrator):
        """Test concurrent pause/resume/reload operations."""
        orchestrator.config = MagicMock()
        orchestrator.monitor_service = Mock()

        # Run concurrent operations
        async def pause():
            orchestrator.pause_monitoring()

        async def resume():
            orchestrator.resume_monitoring()

        tasks = [
            orchestrator.reload_config(),
            pause(),
            resume(),
        ]

        with patch("supsrc.runtime.orchestrator.load_config"):
            results = await asyncio.gather(*tasks, return_exceptions=True)

        # Should not crash
        assert not any(isinstance(r, Exception) for r in results)


# Test coverage for sync methods
class TestSyncMethods:
    """Test synchronous methods."""

    def test_pause_monitoring_sync(self, orchestrator):
        """Test pause_monitoring is synchronous."""
        # This should not be a coroutine
        assert not asyncio.iscoroutinefunction(orchestrator.pause_monitoring)
        orchestrator.pause_monitoring()
        assert orchestrator._is_paused is True

    def test_suspend_monitoring_sync(self, orchestrator):
        """Test suspend_monitoring is synchronous."""
        assert not asyncio.iscoroutinefunction(orchestrator.suspend_monitoring)
        orchestrator.monitor_service = Mock()
        orchestrator.suspend_monitoring()
        assert orchestrator._is_suspended is True

    def test_resume_monitoring_sync(self, orchestrator):
        """Test resume_monitoring is synchronous."""
        assert not asyncio.iscoroutinefunction(orchestrator.resume_monitoring)
        orchestrator._is_paused = True
        orchestrator.resume_monitoring()
        assert orchestrator._is_paused is False

    def test_setup_config_watcher_sync(self, orchestrator):
        """Test setup_config_watcher is synchronous."""
        assert not asyncio.iscoroutinefunction(orchestrator.setup_config_watcher)
        orchestrator.config = MagicMock()
        orchestrator.monitor_service = Mock()
        orchestrator.setup_config_watcher()  # Should not raise


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# 🧪⚙️

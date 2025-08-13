# tests/unit/test_orchestrator.py

"""Unit tests for the refactored WatchOrchestrator component."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from supsrc.config import SupsrcConfig
from supsrc.engines.git import GitEngine
from supsrc.runtime.orchestrator import WatchOrchestrator
from supsrc.state import RepositoryState


@pytest.fixture
def mock_orchestrator(minimal_config: SupsrcConfig) -> WatchOrchestrator:
    """Provides a WatchOrchestrator with a shutdown event."""
    with patch("supsrc.runtime.orchestrator.load_config") as mock_load:
        mock_load.return_value = minimal_config
        orchestrator = WatchOrchestrator(
            config_path=Path("fake.conf"),
            shutdown_event=asyncio.Event(),
        )
        orchestrator.config = minimal_config
        return orchestrator


@pytest.mark.asyncio
class TestOrchestratorLifecycle:
    """Tests for the lifecycle management of the orchestrator."""

    @patch("supsrc.runtime.orchestrator.TUIInterface")
    @patch("supsrc.runtime.orchestrator.ActionHandler")
    @patch("supsrc.runtime.orchestrator.EventProcessor")
    @patch("supsrc.runtime.orchestrator.MonitoringService")
    async def test_run_initializes_all_components(
        self,
        MockMonitoringService: MagicMock,
        MockEventProcessor: MagicMock,
        MockActionHandler: MagicMock,
        MockTUIInterface: MagicMock,
        minimal_config: SupsrcConfig,
    ):
        """Verify that run() instantiates all runtime components."""
        mock_processor_instance = AsyncMock()
        mock_processor_instance.run.return_value = None
        MockEventProcessor.return_value = mock_processor_instance

        mock_monitor_instance = MagicMock()
        mock_monitor_instance.start = MagicMock()
        mock_monitor_instance.stop = AsyncMock()
        MockMonitoringService.return_value = mock_monitor_instance

        with patch("supsrc.runtime.orchestrator.load_config", return_value=minimal_config):
            shutdown_event = asyncio.Event()
            orchestrator = WatchOrchestrator(Path("fake.conf"), shutdown_event)
            
            async def run_and_shutdown():
                run_task = asyncio.create_task(orchestrator.run())
                await asyncio.sleep(0.01)
                shutdown_event.set()
                await run_task

            await run_and_shutdown()

        MockTUIInterface.assert_called_once()
        MockActionHandler.assert_called_once()
        MockEventProcessor.assert_called_once()
        MockMonitoringService.assert_called_once()
        mock_monitor_instance.start.assert_called_once()
        mock_processor_instance.run.assert_called_once()
        mock_monitor_instance.stop.assert_called_once()

    async def test_initialize_repositories_success(self, mock_orchestrator: WatchOrchestrator):
        """Test that repositories are initialized correctly from config."""
        mock_tui = MagicMock()
        await mock_orchestrator._initialize_repositories(mock_orchestrator.config, mock_tui)

        assert "test_repo_1" in mock_orchestrator.repo_states
        assert "test_repo_1" in mock_orchestrator.repo_engines
        state = mock_orchestrator.repo_states["test_repo_1"]
        assert state.repo_id == "test_repo_1"

    async def test_get_repository_details(self, mock_orchestrator: WatchOrchestrator):
        """Test the public API for retrieving repo details for the TUI."""
        repo_id = "test_repo_1"
        mock_engine = AsyncMock(spec=GitEngine)
        mock_engine.get_commit_history.return_value = ["commit1", "commit2"]
        mock_orchestrator.repo_engines = {repo_id: mock_engine}
        mock_orchestrator.config.repositories[repo_id] = MagicMock() # Ensure config exists

        details = await mock_orchestrator.get_repository_details(repo_id)

        assert "commit_history" in details
        assert details["commit_history"] == ["commit1", "commit2"]
        mock_engine.get_commit_history.assert_called_once()


class TestOrchestratorFeatures:
    """Tests for specific orchestrator features like pausing."""

    def test_toggle_repository_pause(self, mock_orchestrator: WatchOrchestrator):
        """Verify that toggling a repository's pause state works correctly."""
        repo_id = "test_repo_1"
        # Ensure the repo exists in the orchestrator's state
        mock_orchestrator.repo_states[repo_id] = RepositoryState(repo_id=repo_id)
        repo_state = mock_orchestrator.repo_states[repo_id]

        # Act & Assert (Pause)
        mock_orchestrator.toggle_repository_pause(repo_id)
        assert repo_state.is_paused is True
        assert repo_state.display_status_emoji == "⏸️"

        # Act & Assert (Resume)
        mock_orchestrator.toggle_repository_pause(repo_id)
        assert repo_state.is_paused is False
        assert repo_state.display_status_emoji == "▶️"  # Default for IDLE

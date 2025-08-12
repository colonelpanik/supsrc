#
# tests/unit/test_orchestrator_features.py
#
"""
Unit tests for specific features of the WatchOrchestrator, such as auto-freeze and pausing.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from supsrc.protocols import RepoStatusResult
from supsrc.runtime.orchestrator import WatchOrchestrator
from supsrc.state import RepositoryState, RepositoryStatus


@pytest.mark.asyncio
async def test_auto_freeze_on_conflict():
    """
    Verify that the orchestrator auto-freezes a repository when a merge conflict is detected.
    """
    # Arrange
    shutdown_event = asyncio.Event()
    orchestrator = WatchOrchestrator(config_path=MagicMock(), shutdown_event=shutdown_event)

    repo_id = "conflicted_repo"
    repo_state = RepositoryState(repo_id=repo_id)
    orchestrator.repo_states[repo_id] = repo_state

    mock_engine = AsyncMock()
    # Simulate a status result indicating a conflict
    mock_engine.get_status.return_value = RepoStatusResult(
        success=True, is_conflicted=True, is_clean=False, is_unborn=False
    )
    orchestrator.repo_engines[repo_id] = mock_engine

    # Mock config and console message
    orchestrator.config = MagicMock()
    orchestrator.config.repositories.get.return_value = MagicMock()
    orchestrator.config.global_config = MagicMock()
    orchestrator._console_message = MagicMock()

    # Mock the subprocess call for macOS notifications
    with patch("subprocess.run") as mock_subprocess_run:
        # Act
        await orchestrator._trigger_action_callback(repo_id)

        # Assert
        assert repo_state.is_frozen is True
        assert repo_state.freeze_reason == "Merge conflicts detected"
        assert repo_state.status == RepositoryStatus.ERROR
        assert repo_state.display_status_emoji == "❌"

        # Verify console message was called
        orchestrator._console_message.assert_called_with(
            "⚠️ CONFLICT DETECTED! Repository frozen.",
            repo_id=repo_id,
            style="bold yellow on red",
            emoji="🧊",
        )

        # Verify that the macOS notification was attempted
        mock_subprocess_run.assert_called_once()
        assert "display notification" in mock_subprocess_run.call_args[0][0][2]


@pytest.mark.slow
@pytest.mark.asyncio
async def test_event_consumption_for_paused_repository():
    """
    Verify that the event consumer skips processing for a paused repository
    and puts the event back on the queue.
    """
    # Arrange
    shutdown_event = asyncio.Event()
    orchestrator = WatchOrchestrator(config_path=MagicMock(), shutdown_event=shutdown_event)
    orchestrator.event_queue = asyncio.Queue()

    repo_id = "paused_repo"
    repo_state = RepositoryState(repo_id=repo_id)
    repo_state.is_paused = True  # Pause the repository
    orchestrator.repo_states[repo_id] = repo_state

    # Put an event onto the queue for the paused repo
    mock_event = MagicMock()
    mock_event.repo_id = repo_id
    await orchestrator.event_queue.put(mock_event)

    # Act
    # Run the consumer for a very short time to process the one event
    consumer_task = asyncio.create_task(orchestrator._consume_events())
    await asyncio.sleep(0.1)
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    # Assert
    # The event should have been put back on the queue
    assert orchestrator.event_queue.qsize() == 1
    # The mock event's process method should not have been called
    mock_event.process.assert_not_called()


def test_toggle_repository_pause():
    """
    Verify that toggling a repository's pause state works correctly.
    """
    # Arrange
    shutdown_event = asyncio.Event()
    orchestrator = WatchOrchestrator(config_path=MagicMock(), shutdown_event=shutdown_event)
    repo_id = "repo_to_pause"
    repo_state = RepositoryState(repo_id=repo_id)
    orchestrator.repo_states[repo_id] = repo_state
    orchestrator._post_tui_state_update = MagicMock() # Mock TUI update

    # Act & Assert (Pause)
    orchestrator.toggle_repository_pause(repo_id)
    assert repo_state.is_paused is True
    assert repo_state.display_status_emoji == "⏸️"
    assert orchestrator._post_tui_state_update.call_count == 1

    # Act & Assert (Resume)
    orchestrator.toggle_repository_pause(repo_id)
    assert repo_state.is_paused is False
    assert repo_state.display_status_emoji == "🧼" # Should revert to IDLE emoji
    assert orchestrator._post_tui_state_update.call_count == 2

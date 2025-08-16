# tests/unit/test_event_processor.py

"""Unit tests for the EventProcessor component."""

import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from supsrc.config import SupsrcConfig
from supsrc.monitor import MonitoredEvent
from supsrc.runtime.action_handler import ActionHandler
from supsrc.runtime.event_processor import DEBOUNCE_DELAY, EventProcessor
from supsrc.runtime.tui_interface import TUIInterface
from supsrc.state import RepositoryState


@pytest.fixture
def mock_action_handler() -> AsyncMock:
    """Provides a mock ActionHandler."""
    return AsyncMock(spec=ActionHandler)


@pytest.fixture
def mock_orchestrator() -> MagicMock:
    """Provides a mock WatchOrchestrator, satisfying the EventProcessor's dependency."""
    mock_orch = MagicMock()
    mock_orch._is_paused = False  # Set the default pause state for tests
    return mock_orch


@pytest.fixture
def event_processor(
    minimal_config: SupsrcConfig,
    mock_action_handler: AsyncMock,
    mock_orchestrator: MagicMock,
) -> EventProcessor:
    """Provides an EventProcessor instance with mocked dependencies."""
    repo_id = "test_repo_1"
    states = {repo_id: RepositoryState(repo_id=repo_id)}
    tui = MagicMock(spec=TUIInterface)
    # The first argument, `orchestrator`, is now provided by the mock_orchestrator fixture.
    return EventProcessor(
        orchestrator=mock_orchestrator,
        config=minimal_config,
        event_queue=asyncio.Queue(),
        shutdown_event=asyncio.Event(),
        action_handler=mock_action_handler,
        repo_states=states,
        tui=tui,
    )


@pytest.mark.asyncio
class TestEventProcessor:
    """Comprehensive tests for the EventProcessor."""

    async def test_event_triggers_action_when_rule_met(
        self, event_processor: EventProcessor, mock_action_handler: AsyncMock, temp_git_repo: Path
    ):
        """Verify an event triggers an action when the rule condition is true."""
        repo_id = "test_repo_1"
        event = MonitoredEvent(repo_id, "modified", temp_git_repo / "f.txt", False)

        with patch("supsrc.runtime.event_processor.check_trigger_condition", return_value=True):
            run_task = asyncio.create_task(event_processor.run())
            await event_processor.event_queue.put(event)
            await asyncio.sleep(DEBOUNCE_DELAY + 0.1)

            # Stop the processor
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task


        mock_action_handler.execute_action_sequence.assert_called_once_with(repo_id)

    async def test_event_starts_timer_when_rule_not_met(
        self, event_processor: EventProcessor, mock_action_handler: AsyncMock, temp_git_repo: Path
    ):
        """Verify an event starts a timer for inactivity rules when the condition is false."""
        repo_id = "test_repo_1"
        event = MonitoredEvent(repo_id, "modified", temp_git_repo / "f.txt", False)

        with patch("supsrc.runtime.event_processor.check_trigger_condition", return_value=False):
            run_task = asyncio.create_task(event_processor.run())
            await event_processor.event_queue.put(event)

            # Wait for the debounce timer to fire and the inactivity timer to be set
            await asyncio.sleep(DEBOUNCE_DELAY + 0.1)

            state = event_processor.repo_states[repo_id]
            assert state.inactivity_timer_handle is not None
            mock_action_handler.execute_action_sequence.assert_not_called()

            # Clean up
            run_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run_task

    async def test_new_event_cancels_previous_timer(
        self, event_processor: EventProcessor, temp_git_repo: Path
    ):
        """Verify that a new event cancels any pending inactivity timer."""
        repo_id = "test_repo_1"
        state = event_processor.repo_states[repo_id]
        mock_timer = MagicMock()
        state.set_inactivity_timer(mock_timer, 30)

        event = MonitoredEvent(repo_id, "modified", temp_git_repo / "f.txt", False)

        with patch("supsrc.runtime.event_processor.check_trigger_condition", return_value=False):
            await event_processor.event_queue.put(event)
            task = asyncio.create_task(event_processor.run())
            await asyncio.sleep(DEBOUNCE_DELAY + 0.1)
            event_processor.shutdown_event.set()
            await task

        mock_timer.cancel.assert_called_once()

    async def test_timer_callback_schedules_action(
        self, event_processor: EventProcessor, mock_action_handler: AsyncMock
    ):
        """Verify the function called by the timer schedules an action."""
        repo_id = "test_repo_1"

        # This now tests the internal _schedule_action method directly
        event_processor._schedule_action(repo_id)

        # Give the event loop a moment to process the created task
        await asyncio.sleep(0.01)

        mock_action_handler.execute_action_sequence.assert_called_once_with(repo_id)

    async def test_shutdown_event_stops_loop(self, event_processor: EventProcessor):
        """Verify the run loop terminates when the shutdown event is set."""
        event_processor.shutdown_event.set()
        task = asyncio.create_task(event_processor.run())

        await asyncio.wait_for(task, timeout=0.1)
        assert task.done()

    async def test_event_consumption_for_paused_repository(self, event_processor: EventProcessor, temp_git_repo: Path):
        """
        Verify that the event consumer skips processing for a paused repository.
        The current implementation ignores (drops) the event.
        """
        repo_id = "test_repo_1"
        repo_state = event_processor.repo_states[repo_id]
        repo_state.is_paused = True

        mock_event = MonitoredEvent(repo_id, "modified", temp_git_repo / "f.txt", False)
        await event_processor.event_queue.put(mock_event)

        # Act: Run the consumer for a very short time to process the one event
        consumer_task = asyncio.create_task(event_processor.run())
        await asyncio.sleep(0.1)
        event_processor.shutdown_event.set() # Stop the loop
        with contextlib.suppress(asyncio.CancelledError):
            await consumer_task

        # Assert: The event should have been consumed and ignored, leaving the queue empty.
        assert event_processor.event_queue.qsize() == 0

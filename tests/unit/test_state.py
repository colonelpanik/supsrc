#
# tests/unit/test_state.py
#
"""
Comprehensive tests for repository state management.
"""

from unittest.mock import Mock

from supsrc.state import RepositoryState, RepositoryStatus


class TestRepositoryState:
    """Test repository state management functionality."""

    def test_initial_state(self) -> None:
        """Test initial repository state."""
        state = RepositoryState(repo_id="test-repo")

        assert state.repo_id == "test-repo"
        assert state.status == RepositoryStatus.IDLE
        assert state.last_change_time is None
        assert state.save_count == 0
        assert state.error_message is None
        assert state.inactivity_timer_handle is None
        assert state.display_status_emoji == "▶️"  # Default emoji for IDLE status

    def test_record_change(self) -> None:
        """Test recording file changes."""
        state = RepositoryState(repo_id="test-repo")

        # Record first change
        state.record_change()

        assert state.status == RepositoryStatus.CHANGED
        assert state.save_count == 1
        assert state.last_change_time is not None
        assert state.display_status_emoji == "📝"  # CHANGED emoji

        # Record second change
        first_time = state.last_change_time
        state.record_change()

        assert state.save_count == 2
        assert state.last_change_time > first_time

    def test_update_status(self) -> None:
        """Test status updates and emoji changes."""
        state = RepositoryState(repo_id="test-repo")

        # Test transition to different statuses
        state.update_status(RepositoryStatus.PROCESSING)
        assert state.status == RepositoryStatus.PROCESSING
        assert state.display_status_emoji == "🔄"

        state.update_status(RepositoryStatus.COMMITTING)
        assert state.status == RepositoryStatus.COMMITTING
        assert state.display_status_emoji == "💾"

        state.update_status(RepositoryStatus.ERROR, "Test error")
        assert state.status == RepositoryStatus.ERROR
        assert state.error_message == "Test error"
        assert state.display_status_emoji == "❌"

        # Test recovery from error
        state.update_status(RepositoryStatus.IDLE)
        assert state.status == RepositoryStatus.IDLE
        assert state.error_message is None
        assert state.display_status_emoji == "▶️"  # Default emoji for IDLE status

    def test_reset_after_action(self) -> None:
        """Test state reset after successful actions."""
        state = RepositoryState(repo_id="test-repo")

        # Set up some state
        state.record_change()
        state.record_change()
        state.update_status(RepositoryStatus.COMMITTING)
        state.last_commit_short_hash = "abc123"
        state.action_description = "Committing changes"

        # Reset after action
        state.reset_after_action()

        assert state.status == RepositoryStatus.IDLE
        assert state.save_count == 0
        assert state.action_description is None
        # Commit info should persist
        assert state.last_commit_short_hash == "abc123"

    def test_inactivity_timer_management(self) -> None:
        """Test inactivity timer handling."""
        state = RepositoryState(repo_id="test-repo")

        # Create mock timer handle
        mock_timer = Mock()
        mock_timer.cancel = Mock()

        # Set timer
        state.set_inactivity_timer(mock_timer, 60)
        assert state.inactivity_timer_handle == mock_timer

        # Cancel timer
        state.cancel_inactivity_timer()
        mock_timer.cancel.assert_called_once()
        assert state.inactivity_timer_handle is None

        # Test canceling when no timer exists
        state.cancel_inactivity_timer()  # Should not raise


class TestRepositoryStatusEnum:
    """Test repository status enumeration."""

    def test_all_statuses_have_emojis(self) -> None:
        """Ensure all status values have corresponding emojis."""
        from supsrc.state import STATUS_EMOJI_MAP

        for status in RepositoryStatus:
            assert status in STATUS_EMOJI_MAP, f"Missing emoji for {status}"


# 🧪📊

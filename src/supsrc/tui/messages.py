#
# supsrc/tui/messages.py
#
"""
Defines custom messages for the Textual User Interface (TUI).
"""

from typing import (
    Any,
)  # Ensure Any is kept if details in RepoDetailUpdate uses it, or other classes.

# If not, Any can be removed. For now, keeping it.
from textual.message import Message

# Import types
from supsrc.types import RepositoryStatesMap


class StateUpdate(Message):
    """Message to update the main repository status table in the TUI."""

    ALLOW_BUBBLE = True  # Or False if only handled by App

    def __init__(self, repo_states: RepositoryStatesMap) -> None:
        self.repo_states = repo_states
        super().__init__()


class LogMessageUpdate(Message):
    """Message to send a new log entry to the TUI's event log."""

    ALLOW_BUBBLE = True  # Or False if only handled by App

    def __init__(self, repo_id: str | None, level: str, message: str) -> None:
        self.repo_id = repo_id
        self.level = level
        self.message = message
        super().__init__()


class RepoDetailUpdate(Message):
    """Message to update the repo detail pane with fetched information."""

    ALLOW_BUBBLE = True  # Or False if only handled by App

    def __init__(self, repo_id: str, details: dict[str, Any]) -> None:
        self.repo_id = repo_id
        self.details = details  # This will contain {"commit_history": [...]}
        super().__init__()


# 🔼⚙️

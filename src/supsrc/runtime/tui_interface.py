# src/supsrc/runtime/tui_interface.py

"""
Provides a thread-safe interface for communicating with the Textual TUI.
"""

from typing import TYPE_CHECKING, Optional

import attrs
import structlog

from supsrc.state import RepositoryState
from supsrc.telemetry import StructLogger

# Conditional imports for TUI components to avoid hard dependency
try:
    if TYPE_CHECKING:
        from supsrc.tui.app import SupsrcTuiApp
    from supsrc.tui.messages import LogMessageUpdate, StateUpdate
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False
    SupsrcTuiApp = None  # type: ignore
    StateUpdate = None  # type: ignore
    LogMessageUpdate = None  # type: ignore

log: StructLogger = structlog.get_logger("runtime.tui_interface")


class TUIInterface:
    """A thread-safe bridge for communicating with the Textual UI."""

    def __init__(self, app: Optional["SupsrcTuiApp"]):
        self.app = app
        self.is_active = bool(app and TEXTUAL_AVAILABLE)
        if self.is_active:
            log.debug("TUI Interface initialized and active.")

    def post_state_update(self, states: dict[str, RepositoryState]) -> None:
        """Posts the current repository states to the TUI."""
        if not self.is_active or not self.app or not StateUpdate:
            return

        try:
            # Create a shallow copy of states for thread safety.
            # attrs.evolve creates new instances, ensuring immutability across threads.
            states_copy = {rid: attrs.evolve(state) for rid, state in states.items()}
            self.app.post_message(StateUpdate(states_copy))
        except Exception as e:
            # This log won't go to the TUI to prevent loops, but will go to file if configured.
            log.warning("Failed to post state update to TUI", error=str(e), exc_info=False)

    def post_log_update(self, repo_id: str | None, level: str, message: str) -> None:
        """Posts a log message to the TUI."""
        if not self.is_active or not self.app or not LogMessageUpdate:
            return

        try:
            # The formatter in TextualLogHandler will create the rich markup.
            self.app.post_message(LogMessageUpdate(repo_id, level.upper(), message))
        except Exception as e:
            log.warning("Failed to post log message to TUI", error=str(e), exc_info=False)

#
# supsrc/tui/logging_handler.py
#
"""
Custom logging handler for integrating structlog output with the Textual TUI.
"""

import logging
from typing import TYPE_CHECKING

import structlog  # For ConsoleRenderer

# from structlog.dev import ConsoleRenderer # Could be more specific
# Assuming LogMessageUpdate is in supsrc.tui.messages
from supsrc.tui.messages import LogMessageUpdate

if TYPE_CHECKING:
    from supsrc.tui.app import SupsrcTuiApp


class TextualLogHandler(logging.Handler):
    """
    A logging handler that forwards formatted log records to a SupsrcTuiApp
    via a LogMessageUpdate message.
    """

    def __init__(self, app: "SupsrcTuiApp", level: int = logging.NOTSET) -> None:
        """
        Initialize the handler.

        Args:
            app: The SupsrcTuiApp instance to post messages to.
            level: The logging level for this handler.
        """
        super().__init__(level=level)
        self.app = app
        # The ConsoleRenderer from structlog can be used to format messages
        # with Rich markup, which Textual can then render.
        # This handler will rely on the formatter configured in the main logging setup
        # (which should be ProcessorFormatter using ConsoleRenderer)
        # to provide the already formatted string via self.format(record).
        # So, an explicit renderer instance here might not be strictly needed for formatting,
        # but it was in the requirements. Let's keep it for now.
        self.renderer = structlog.dev.ConsoleRenderer(colors=True)  # As per requirements

    def emit(self, record: logging.LogRecord) -> None:
        """
        Format and emit a log record to the TUI.

        This method is called by the logging system for each log record.
        It formats the record and then posts a LogMessageUpdate to the
        associated TUI application.

        Args:
            record: The log record to emit.
        """
        try:
            # Attempt to get 'repo_id' from the log record, defaulting to 'SYSTEM'.
            # structlog adds bound variables directly to the record.
            repo_id: str = getattr(record, "repo_id", "SYSTEM")

            # The record.msg is usually the 'event' from structlog.
            # The self.format(record) call will use the formatter set on this handler
            # (or the root logger's formatter if none is set here).
            # In our setup, structlog.stdlib.ProcessorFormatter is used, which
            # applies all structlog processors including the final renderer (e.g., ConsoleRenderer).
            message_str: str = self.format(record)

            # Create the message object for the TUI
            log_update_msg = LogMessageUpdate(
                repo_id=repo_id,
                level=record.levelname,  # e.g., "INFO", "WARNING"
                message=message_str,
            )

            # Post the message to the TUI application's message queue
            if self.app and hasattr(self.app, "post_message"):
                self.app.post_message(log_update_msg)
            else:
                # Fallback if app is not available or misconfigured (should not happen in normal operation)
                pass

        except Exception:
            # Fallback for any errors during log emission to TUI
            # (e.g., if TUI is closing or an unexpected error occurs)
            # We print to stderr to avoid a loop if this handler itself is part of the failing logging chain.
            # Optionally, print the original message as well if self.format(record) failed
            try:
                pass
            except Exception:
                pass  # Avoid further errors if getMessage itself fails


# 🪵🎨

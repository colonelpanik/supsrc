#
# supsrc/telemetry/logger/base.py
#
"""
Base logging setup for the supsrc application using structlog.
"""

import logging
import sys
from typing import TYPE_CHECKING, Any, Optional

import structlog
from structlog.typing import FilteringBoundLogger

if TYPE_CHECKING:
    from supsrc.tui.app import SupsrcTuiApp # Adjust path if needed

from supsrc.tui.logging_handler import TextualLogHandler

# Use absolute imports for processors
from supsrc.telemetry.logger.processors import (
    add_emoji_processor,
    # add_padded_logger_processor, # Removed
    remove_extra_keys_processor,
)

# --- Constants ---
BASE_LOGGER_NAME = "supsrc" # Used for filtering/formatting
# Emojis remain useful for the custom processor
LOG_EMOJIS = {
    logging.DEBUG: "🐛", logging.INFO: "ℹ️", logging.WARNING: "⚠️",
    logging.ERROR: "❌", logging.CRITICAL: "💥",
    "load": "📄", "validate": "✅", "fail": "🚫", "path": "📁", "time": "⏱️",
    "success": "🎉", "general": "➡️",
}

# --- Module level TUI state ---
_is_tui_active: bool = False
# --- Core structlog Setup Function ---

def setup_logging(
    level: int = logging.INFO,
    json_logs: bool = False,
    log_file: str | None = None,
    file_only: bool = False,  # New parameter for file-only logging
    tui_app_instance: Optional["SupsrcTuiApp"] = None
) -> None:
    """
    Configures structlog for the entire supsrc application.

    Args:
        level: The minimum logging level (e.g., logging.DEBUG).
        json_logs: If True, format console output as JSON. Otherwise, use colored console output.
        log_file: Optional path for JSON file logging.
    """
    log_level_name = logging.getLevelName(level)
    # Use stderr for setup messages to avoid interfering with potential JSON stdout
    # print(f"--- Setting up structlog logging (Level: {log_level_name}, JSON: {json_logs}, File: {log_file}) ---", file=sys.stderr)

    # Shared processors used by both console/file and potentially JSON outputs
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars, # Merge contextvars if used
        structlog.stdlib.add_logger_name,      # Adds logger name (e.g., 'supsrc.config.loader')
        structlog.stdlib.add_log_level,        # Adds log level name (e.g., 'info')
        structlog.processors.TimeStamper(fmt="iso", utc=True), # Add ISO timestamp in UTC
        # --- Custom processors ---
        add_emoji_processor,                   # Custom: Add emoji based on level or key
        # add_padded_logger_processor,         # <<< REMOVED >>>
        remove_extra_keys_processor,           # Custom: Clean up temporary keys like emoji_key
        # --- End custom processors ---
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter, # Prepare for stdlib formatting/handling
    ]

    # Configure structlog itself
    structlog.configure(
        processors=shared_processors,
        logger_factory=structlog.stdlib.LoggerFactory(), # Use stdlib loggers underneath
        wrapper_class=structlog.stdlib.BoundLogger,    # Standard wrapper
        cache_logger_on_first_use=True,
    )

    # Configure the underlying standard library logging system
    # This is where handlers and the final formatting/rendering happen

    # Define the final renderer based on json_logs flag
    if json_logs:
        # Use JSON renderer for console/file
        final_renderer = structlog.processors.JSONRenderer(sort_keys=True) # Add sort_keys for consistency
    else:
        # Use colored console renderer for development
        # Removed 'padded_logger' from the format string implicitly
        # The default format string will likely use 'logger' and 'level' etc.
        final_renderer = structlog.dev.ConsoleRenderer(
             colors=True, # Ensure colors are enabled
             level_styles=structlog.dev.ConsoleRenderer.get_default_level_styles(),
             # Example simplified format: rely more on key-value pairs
             # "{timestamp} [{level}] {logger}: {event} {emoji}"
        )

    # Create the formatter that integrates structlog processors with stdlib handlers
    formatter = structlog.stdlib.ProcessorFormatter(
        # The foreign_pre_chain is applied to logs NOT originating from structlog
        # but captured by our handlers (e.g., from libraries using standard logging)
        # foreign_pre_chain=[structlog.stdlib.add_log_level], # Example
        # The final processor in this chain is the renderer
        processor=final_renderer,
    )

    # Configure the standard library root logger handler (for console)
    root_logger = logging.getLogger() # Get stdlib root logger
    root_logger.handlers.clear()      # Clear existing handlers
    root_logger.setLevel(level)     # Set level on the root logger first

    # Log initial message using a structlog logger AFTER configuration
    # This needs to be defined early so it can be used by subsequent setup steps.
    slog = structlog.get_logger(BASE_LOGGER_NAME)

    # Console Handler (conditionally added based on TUI status and file_only)
    if not _is_tui_active and not file_only:
        # Standard console handler for non-TUI, non-file-only mode
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
        slog.debug("Standard StreamHandler added for console output.")
    elif _is_tui_active and not file_only:
        # TUI is active, TextualLogHandler is responsible for TUI output.
        # Standard console output via StreamHandler is suppressed.
        slog.debug("TUI active: Standard StreamHandler to sys.stdout is suppressed even if file_only is false.")
    elif file_only and not log_file:
        # This warning remains valid
        # Using direct print as slog might not be fully configured for console output here if file_only is true
        print(f"WARNING: Logging configured with file_only=True but no log_file was provided. No log output will be generated.", file=sys.stderr)
    elif file_only and log_file:
        # This info remains valid
        # Using slog, as it will go to the file if file_only=True and log_file is set.
        slog.info(f"Console logging via StreamHandler suppressed (file_only=True). Logs are being written to '{log_file}'.")


    # Configure File Handler (Optional, always JSON for machine readability)
    if log_file:
        try:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            # Use a separate formatter for the file, forcing JSON output
            file_formatter = structlog.stdlib.ProcessorFormatter(
                processor=structlog.processors.JSONRenderer(sort_keys=True),
            )
            file_handler.setFormatter(file_formatter)
            file_handler.setLevel(level) # Set level for file handler too
            root_logger.addHandler(file_handler) # Add file handler to root
            slog.info(f"File logging enabled to '{log_file}'") # Use slog
        except Exception as e:
            # Use standard logging here if slog itself might be part of the problem with file handler
            logging.getLogger(BASE_LOGGER_NAME).error(f"Failed to setup file logging to '{log_file}': {e}", exc_info=True)

    # Add TextualLogHandler if TUI mode is active
    if _is_tui_active and tui_app_instance:
        slog.debug("TUI mode active, attempting to add TextualLogHandler.")

        textual_handler = TextualLogHandler(app=tui_app_instance)
        textual_handler.setLevel(level) # Use the same overall log level
        textual_handler.setFormatter(formatter) # Use the same formatter as the console handler

        root_logger.addHandler(textual_handler)
        slog.info("TextualLogHandler added to root logger for TUI.")
    elif _is_tui_active and not tui_app_instance:
        slog.warning("TUI mode is active but no TUI app instance was provided to logging setup.")

    # Final log message about initialization status
    slog.info(
        "structlog logging initialization complete",
        log_level=log_level_name,
        json_console_format=json_logs,
        console_output_enabled=(not _is_tui_active and not file_only),
        log_file=log_file or "None",
        file_only_requested=file_only,
        tui_mode_active=_is_tui_active,
        tui_handler_added=(_is_tui_active and tui_app_instance is not None)
    )


# --- Logger Type Hint ---
# Define a type hint for the logger returned by structlog
StructLogger = FilteringBoundLogger # Or more specific type if needed

# 🔼⚙️

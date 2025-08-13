# src/supsrc/telemetry/logger/base.py

import logging
import sys
from typing import TYPE_CHECKING, Optional

import structlog
from structlog.typing import FilteringBoundLogger

from supsrc.telemetry.logger.processors import (
    add_emoji_processor,
    remove_extra_keys_processor,
)

try:
    from supsrc.tui.logging_handler import TextualLogHandler
    HAS_TUI = True
except ImportError:
    TextualLogHandler = None
    HAS_TUI = False

if TYPE_CHECKING:
    from supsrc.tui.app import SupsrcTuiApp


BASE_LOGGER_NAME = "supsrc"
LOG_EMOJIS = {
    logging.DEBUG: "🐛",
    logging.INFO: "ℹ️",
    logging.WARNING: "⚠️",
    logging.ERROR: "❌",
    logging.CRITICAL: "💥",
    "load": "📄",
    "validate": "✅",
    "fail": "🚫",
    "path": "📁",
    "time": "⏱️",
    "success": "🎉",
    "general": "➡️",
}


def setup_logging(
    level: int = logging.INFO,
    json_logs: bool = False,
    log_file: str | None = None,
    file_only: bool = False,
    tui_app_instance: Optional["SupsrcTuiApp"] = None,
    headless_mode: bool = False,
) -> None:
    """Configures structlog for the entire application."""
    is_tui_mode = tui_app_instance is not None
    log_level_name = logging.getLevelName(level)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        add_emoji_processor,
        remove_extra_keys_processor,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]

    structlog.configure(
        processors=shared_processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    if json_logs:
        final_renderer = structlog.processors.JSONRenderer(sort_keys=True)
    elif headless_mode:
        try:
            from rich.console import Console
            safe_console = Console(file=sys.stdout, hijack=False)
            final_renderer = structlog.dev.ConsoleRenderer(console=safe_console, colors=True)
        except (ImportError, TypeError):
            final_renderer = structlog.dev.ConsoleRenderer(colors=True)
    else:
        final_renderer = structlog.dev.ConsoleRenderer(colors=True)

    formatter = structlog.stdlib.ProcessorFormatter(processor=final_renderer)

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        handler.close()
        root_logger.removeHandler(handler)
    root_logger.setLevel(level)

    slog = structlog.get_logger(BASE_LOGGER_NAME)

    if not is_tui_mode and not file_only:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
        slog.debug("Standard StreamHandler added for console output.")

    if log_file:
        try:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_formatter = structlog.stdlib.ProcessorFormatter(
                processor=structlog.processors.JSONRenderer(sort_keys=True)
            )
            file_handler.setFormatter(file_formatter)
            file_handler.setLevel(level)
            root_logger.addHandler(file_handler)
            slog.info(f"File logging enabled to '{log_file}'")
        except Exception as e:
            slog.error(f"Failed to setup file logging to '{log_file}': {e}", exc_info=True)

    if is_tui_mode and tui_app_instance:
        if HAS_TUI and TextualLogHandler:
            textual_handler = TextualLogHandler(app=tui_app_instance)
            textual_handler.setLevel(level)
            textual_handler.setFormatter(formatter)
            root_logger.addHandler(textual_handler)
            slog.info("TextualLogHandler added for TUI.")
        else:
            slog.error("TUI mode active but textual is not installed.")

    slog.info(
        "structlog logging initialization complete",
        log_level=log_level_name,
        json_console_format=json_logs,
        console_output_enabled=(not is_tui_mode and not file_only),
        log_file=log_file or "None",
        tui_mode_active=is_tui_mode,
    )


StructLogger = FilteringBoundLogger

# 🔼⚙️

#
# supsrc/cli/utils.py
#
"""
Shared utility functions and decorators for CLI commands.
"""

import logging
from typing import Any

import click
import structlog

# Alias the import as core_setup_logging
from supsrc.telemetry.logger import setup_logging as core_setup_logging

# Logger for this specific module (though it's mostly providing utilities)
log = structlog.get_logger("cli.utils")

# Define choices based on standard logging levels
LOG_LEVEL_CHOICES = click.Choice(list(logging._nameToLevel.keys()), case_sensitive=False)


def logging_options(f):
    """Decorator to add logging options to any command."""
    f = click.option(
        "-l",
        "--log-level",
        type=LOG_LEVEL_CHOICES,
        default=None,  # None means inherit from parent or use global default
        envvar="SUPSRC_LOG_LEVEL",
        help="Set the logging level (overrides config file).",
    )(f)
    f = click.option(
        "--log-file",
        type=click.Path(dir_okay=False, writable=True, resolve_path=True),
        default=None,
        envvar="SUPSRC_LOG_FILE",
        help="Path to write logs to a file (JSON format). Suppresses console output if --file-only-logs is used (default for TUI).",
    )(f)
    f = click.option(
        "--json-logs",
        is_flag=True,
        default=None,  # None means inherit from parent or use global default
        envvar="SUPSRC_JSON_LOGS",
        help="Output console logs as JSON.",
    )(f)
    # Option to control if console output is suppressed when log_file is used.
    # TUI might set this to True by default.
    f = click.option(
        "--file-only-logs",
        is_flag=True,
        default=False,  # Default to False for most CLI commands
        help="Suppress console output when --log-file is specified. TUI defaults this to true.",
    )(f)
    return f


def setup_logging_from_context(
    ctx: click.Context,
    # Parameters to allow specific command to override context or defaults
    local_log_level: str | None = None,
    local_log_file: str | None = None,
    local_json_logs: bool | None = None,
    local_file_only_logs: bool | None = None,  # For specific command needs, e.g. TUI
    default_log_level: str = "WARNING",  # Global default if nothing else is set
    tui_app_instance: Any | None = None,
) -> None:
    """
    Setup logging using context values, allowing local overrides.
    Context values are assumed to be set by the main CLI group from its own options.
    """
    # Determine the effective setting, prioritizing local command options,
    # then context (which includes CLI global options & env vars), then a fallback default.
    log_level_str = local_log_level or ctx.obj.get("LOG_LEVEL") or default_log_level
    log_file_path = local_log_file or ctx.obj.get("LOG_FILE")

    # For flags, None means "not set by this command", so check context
    # If context also has None (meaning not set by global CLI option), then use a default (False).
    use_json_logs = (
        local_json_logs if local_json_logs is not None else ctx.obj.get("JSON_LOGS", False)
    )

    # file_only_logs: True if this command sets it, else check context, else default to False.
    # This allows TUI to default to True while other commands default to False.
    # However, the new requirement for file_only is simpler: it's true if a log file is specified.
    # The --file-only-logs flag from click options is effectively ignored here if we follow the new rule strictly.
    # For this change, I will prioritize the new rule: file_only=(log_file_path is not None).
    # The `use_file_only_logs` variable (derived from the click option) is no longer the direct determinant for core_setup_logging's file_only.
    # We might need to reconcile if the click option should still have an effect.
    # For now, implementing as per the "Correct usage" comment.

    # Get numeric level
    numeric_level = logging.getLevelName(log_level_str.upper())
    if not isinstance(numeric_level, int):
        # Fallback if level name is somehow invalid
        log.warning(
            "Invalid log level name provided, defaulting to INFO.",
            invalid_level=log_level_str,
        )
        numeric_level = logging.INFO
        log_level_str = "INFO"

    final_file_only_setting = log_file_path is not None

    core_setup_logging(
        level=numeric_level,
        json_logs=use_json_logs,  # This remains from click options/context
        log_file=log_file_path,
        file_only=final_file_only_setting,  # Corrected usage: true if log_file is specified
        tui_app_instance=tui_app_instance,
    )

    log.debug(
        "CLI logging initialized via utils",
        level=log_level_str,
        file=log_file_path or "console",
        json=use_json_logs,
        file_only_effective=final_file_only_setting,
    )


# ⚙️🛠️

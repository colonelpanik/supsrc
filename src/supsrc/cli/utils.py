# src/supsrc/cli/utils.py

import logging
from typing import Any

import click
import structlog

from supsrc.telemetry.logger import setup_logging as core_setup_logging

log = structlog.get_logger("cli.utils")

LOG_LEVEL_CHOICES = click.Choice(list(logging._nameToLevel.keys()), case_sensitive=False)


def logging_options(f):
    """Decorator to add logging options to any command."""
    f = click.option(
        "-l",
        "--log-level",
        type=LOG_LEVEL_CHOICES,
        default=None,
        envvar="SUPSRC_LOG_LEVEL",
        help="Set the logging level (overrides config file).",
    )(f)
    f = click.option(
        "--log-file",
        type=click.Path(dir_okay=False, writable=True, resolve_path=True),
        default=None,
        envvar="SUPSRC_LOG_FILE",
        help="Path to write logs to a file (JSON format).",
    )(f)
    f = click.option(
        "--json-logs",
        is_flag=True,
        default=None,
        envvar="SUPSRC_JSON_LOGS",
        help="Output console logs as JSON.",
    )(f)
    return f


def setup_logging_from_context(
    ctx: click.Context,
    local_log_level: str | None = None,
    local_log_file: str | None = None,
    local_json_logs: bool | None = None,
    default_log_level: str = "INFO",
    tui_app_instance: Any | None = None,
    headless_mode: bool = False,
) -> None:
    """
    Setup logging using context values, allowing local overrides.
    """
    log_level_str = local_log_level or ctx.obj.get("LOG_LEVEL") or default_log_level
    log_file_path = local_log_file or ctx.obj.get("LOG_FILE")
    use_json_logs = local_json_logs if local_json_logs is not None else ctx.obj.get("JSON_LOGS", False)

    numeric_level = logging.getLevelName(log_level_str.upper())
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO
        log_level_str = "INFO"

    file_only = (tui_app_instance is not None) and (log_file_path is not None)

    core_setup_logging(
        level=numeric_level,
        json_logs=use_json_logs,
        log_file=log_file_path,
        file_only=file_only,
        tui_app_instance=tui_app_instance,
        headless_mode=headless_mode,
    )

    log.debug(
        "CLI logging initialized via utils",
        level=log_level_str,
        file=log_file_path or "console",
        json=use_json_logs,
        file_only=file_only,
        headless=headless_mode,
    )

# ⚙️🛠️

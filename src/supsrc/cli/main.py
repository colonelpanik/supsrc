#
# supsrc/cli/main.py
#
"""
Main CLI entry point for supsrc using Click.
Handles global options like logging level.
"""

import logging
import sys
from importlib.metadata import PackageNotFoundError, version

import click
import structlog

from supsrc.cli.config_cmds import config_cli
from supsrc.cli.watch_cmds import watch_cli
from supsrc.telemetry import StructLogger  # Import type hint

# Use absolute imports
from supsrc.telemetry.logger import setup_logging

try:
    __version__ = version("supsrc")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"

# Logger for this specific module
log: StructLogger = structlog.get_logger("cli.main")

# Define choices based on standard logging levels
LOG_LEVEL_CHOICES = click.Choice(
    list(logging._nameToLevel.keys()), case_sensitive=False
)

@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version", package_name="supsrc")
@click.option(
    "-l", "--log-level",
    type=LOG_LEVEL_CHOICES,
    default="INFO",
    show_default=True,
    envvar="SUPSRC_LOG_LEVEL",
    help="Set the logging level (overrides config file, env var SUPSRC_LOG_LEVEL).",
    show_envvar=True,
)
@click.option(
    "--log-file",
    type=click.Path(dir_okay=False, writable=True, resolve_path=True),
    default=None,
    envvar="SUPSRC_LOG_FILE",
    help="Path to write logs to a file (JSON format) (env var SUPSRC_LOG_FILE).",
    show_envvar=True,
)
@click.option(
    "--json-logs",
    is_flag=True,
    default=False,
    envvar="SUPSRC_JSON_LOGS",
    help="Output console logs as JSON (env var SUPSRC_JSON_LOGS).",
    show_envvar=True,
)
@click.pass_context # Pass context to store/retrieve shared options
def cli(ctx: click.Context, log_level: str, log_file: str | None, json_logs: bool):
    """
    Supsrc: Automated Git commit/push utility.

    Monitors repositories and performs Git actions based on rules.
    Configuration precedence: CLI options > Environment Variables > Config File > Defaults.
    """
    # Ensure context object exists
    ctx.ensure_object(dict)
    # Store options in context for subcommands to access
    # These values already reflect Click's precedence (CLI > Env Var > Default)
    ctx.obj["LOG_LEVEL"] = log_level
    ctx.obj["LOG_FILE"] = log_file
    ctx.obj["JSON_LOGS"] = json_logs

    # --- Setup Logging EARLY ---
    # Get numeric level AFTER validation by Click
    log_level_numeric = logging.getLevelName(log_level.upper())
    setup_logging(
        level=log_level_numeric,
        json_logs=json_logs,
        log_file=log_file
    )
    log.debug("CLI context initialized", args=sys.argv, options=ctx.obj)


# Add command groups to the main CLI group
cli.add_command(config_cli)
cli.add_command(watch_cli)


if __name__ == "__main__":
    # This allows running the CLI via 'python -m supsrc.cli.main'
    # or directly if needed, but entry point script is preferred.
    cli()

# 🔼⚙️

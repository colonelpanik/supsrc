#
# supsrc/cli/main.py
#
"""
Main CLI entry point for supsrc using Click.
Handles global options like logging level.
"""

from importlib.metadata import PackageNotFoundError, version

import click
import structlog

from supsrc.cli.config_cmds import config_cli
from supsrc.cli.tail_cmds import tail_cli

# Import logging utilities from the new cli.utils module
from supsrc.cli.utils import logging_options, setup_logging_from_context
from supsrc.cli.watch_cmds import watch_cli
from supsrc.telemetry import StructLogger

try:
    __version__ = version("supsrc")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"

# Logger for this specific module
log: StructLogger = structlog.get_logger("cli.main")


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version", package_name="supsrc")
@logging_options  # This now comes from cli.utils
@click.pass_context
def cli(
    ctx: click.Context,
    log_level: str | None,
    log_file: str | None,
    json_logs: bool | None,
    file_only_logs: bool | None,  # Added by logging_options from utils
):
    """
    Supsrc: Automated Git commit/push utility.

    Monitors repositories and performs Git actions based on rules.
    Configuration precedence: CLI options > Environment Variables > Config File > Defaults.
    """
    # Ensure context object exists
    ctx.ensure_object(dict)

    # Store options in context for subcommands to access.
    # These values reflect Click's precedence (CLI > Env Var > Default)
    # `log_level` will be None if not provided, setup_logging_from_context handles default.
    ctx.obj["LOG_LEVEL"] = log_level
    ctx.obj["LOG_FILE"] = log_file
    # json_logs and file_only_logs are flags; store their actual boolean value.
    # None means "not set by CLI", False is the effective default from the decorator.
    ctx.obj["JSON_LOGS"] = json_logs if json_logs is not None else False
    ctx.obj["FILE_ONLY_LOGS"] = file_only_logs if file_only_logs is not None else False

    # Initial minimal logging setup for the CLI itself before subcommands run their own.
    # Subcommands will call setup_logging_from_context again, which is fine.
    setup_logging_from_context(
        ctx, default_log_level="WARNING"
    )  # Default to WARNING for CLI group itself
    log.debug(
        "Main CLI group initialized",
        log_level=log_level,
        log_file=log_file,
        json_logs=json_logs,
        file_only_logs=file_only_logs,
    )


# Add command groups to the main CLI group
cli.add_command(config_cli)
cli.add_command(tail_cli)
cli.add_command(watch_cli)

if __name__ == "__main__":
    cli()

# 🖥️⚙️

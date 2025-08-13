# src/supsrc/cli/config_cmds.py

from pathlib import Path

import click
import structlog

from supsrc.cli.utils import logging_options, setup_logging_from_context
from supsrc.config import load_config
from supsrc.exceptions import ConfigurationError
from supsrc.telemetry import StructLogger

try:
    from rich.pretty import pretty_repr
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

log: StructLogger = structlog.get_logger("cli.config")


@click.group(name="config")
def config_cli():
    """Commands for inspecting and validating configuration."""
    pass


@config_cli.command(name="show")
@click.option(
    "-c",
    "--config-path",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, readable=True, path_type=Path),
    default=Path("supsrc.conf"),
    show_default=True,
    envvar="SUPSRC_CONF",
    help="Path to the supsrc configuration file (env var SUPSRC_CONF).",
    show_envvar=True,
)
@logging_options
@click.pass_context
def show_config(ctx: click.Context, config_path: Path, **kwargs):
    """Load, validate, and display the configuration."""
    setup_logging_from_context(
        ctx,
        local_log_level=kwargs.get("log_level"),
        local_log_file=kwargs.get("log_file"),
        local_json_logs=kwargs.get("json_logs"),
    )
    log.info("Executing 'config show' command", config_path=str(config_path))

    try:
        config = load_config(config_path)
        log.debug("Configuration loaded successfully by 'show' command.")

        if RICH_AVAILABLE:
            output_str = pretty_repr(config, expand_all=True)
            click.echo(output_str)
        else:
            import pprint
            import io
            with io.StringIO() as buffer:
                pprint.pprint(config, stream=buffer)
                output_str = buffer.getvalue()
            click.echo(output_str)

        disabled_count = sum(1 for repo in config.repositories.values() if not repo._path_valid)
        if disabled_count > 0:
            log.warning(
                f"{disabled_count} repository path(s) were invalid and auto-disabled.",
                count=disabled_count,
            )
        else:
            log.info("All repository paths validated successfully.")

    except ConfigurationError as e:
        log.error("Failed to load or validate configuration", error=str(e), exc_info=True)
        click.echo(f"Error: Configuration problem in '{config_path}':\n{e}", err=True)
        ctx.exit(1)
    except Exception as e:
        log.critical(
            "An unexpected error occurred during 'config show'",
            error=str(e),
            exc_info=True,
        )
        click.echo(f"Error: An unexpected issue occurred: {e}", err=True)
        ctx.exit(2)

# 🔼⚙️

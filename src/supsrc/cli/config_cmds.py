#
# supsrc/cli/config_cmds.py
#
"""
CLI commands related to configuration management for supsrc.
"""

from pathlib import Path

import click
import structlog

# Use relative imports within the package
from supsrc.config import load_config
from supsrc.exceptions import ConfigurationError
from supsrc.telemetry import StructLogger  # Import type hint
# Import logging utilities
from supsrc.cli.utils import logging_options, setup_logging_from_context

# Import rich if available for pretty printing
try:
    import rich.pretty
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

log: StructLogger = structlog.get_logger("cli.config")

# Create a command group for config-related commands
@click.group(name="config")
def config_cli():
    """Commands for inspecting and validating configuration."""
    pass


@config_cli.command(name="show")
@click.option(
    "-c", "--config-path",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, readable=True, path_type=Path),
    default=Path("supsrc.conf"),
    show_default=True,
    envvar="SUPSRC_CONF", # <<< Added Environment Variable Support
    help="Path to the supsrc configuration file (env var SUPSRC_CONF).",
    show_envvar=True, # <<< Show env var in help message
)
@logging_options # Add decorator
@click.pass_context # Get context from the parent group (for log level etc)
def show_config(ctx: click.Context, config_path: Path, **kwargs): # Add **kwargs to accept options
    """Load, validate, and display the configuration."""
    # Setup logging for this command
    setup_logging_from_context(
        ctx,
        local_log_level=kwargs.get("log_level"),
        local_log_file=kwargs.get("log_file"),
        local_json_logs=kwargs.get("json_logs"),
        local_file_only_logs=kwargs.get("file_only_logs")
        # default_log_level can be omitted to use the one from utils.py or set by main cli
    )
    log.info("Executing 'config show' command", config_path=str(config_path))

    try:
        # load_config now handles env var overrides for global defaults internally
        config = load_config(config_path)
        log.debug("Configuration loaded successfully by 'show' command.")

        if RICH_AVAILABLE:
            rich.pretty.pprint(config, expand_all=True)
        else:
            # Basic fallback pretty print
            pass

        # Check for disabled repos and inform user
        disabled_count = sum(1 for repo in config.repositories.values() if not repo._path_valid)
        if disabled_count > 0:
            log.warning(f"{disabled_count} repository path(s) were invalid and auto-disabled.", count=disabled_count)
        else:
             log.info("All repository paths validated successfully.")


    except ConfigurationError as e:
        log.error("Failed to load or validate configuration", error=str(e), exc_info=True)
        # Use click.echo for consistent CLI output, especially for errors
        click.echo(f"Error: Configuration problem in '{config_path}':\n{e}", err=True)
        ctx.exit(1) # Exit with error code
    except Exception as e:
        log.critical("An unexpected error occurred during 'config show'", error=str(e), exc_info=True)
        click.echo(f"Error: An unexpected issue occurred: {e}", err=True)
        ctx.exit(2)

# 🔼⚙️

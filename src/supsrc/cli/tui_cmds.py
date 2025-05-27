#
# supsrc/cli/tui_cmds.py
#
"""
CLI command to launch the Textual User Interface.
"""

import asyncio
import sys
from pathlib import Path

import click
import structlog

# Import logging utilities from the new cli.utils module
from supsrc.cli.utils import setup_logging_from_context, logging_options
from supsrc.tui.app import SupsrcTuiApp
import supsrc.telemetry.logger.base as telemetry_logger_base

log = structlog.get_logger("cli.tui")

@click.command("tui")
@click.option(
    "-c", "--config", "config_path_str",
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help="Path to the supsrc configuration file.",
    required=True, # Make it required for now for simplicity
)
@logging_options # Reuse logging options
@click.pass_context
def tui_cli(
    ctx: click.Context,
    config_path_str: str,
    log_level: str | None,    # Explicitly define
    log_file: str | None,     # Explicitly define
    json_logs: bool | None,   # Explicitly define
    file_only_logs: bool | None # Explicitly define (added by @logging_options)
    # **kwargs removed as all known options from @logging_options are now explicit
):
    """Launch the Supsrc Textual User Interface."""

    config_path = Path(config_path_str)
    # Log basic info before full logging setup, if necessary, or rely on post-setup logging.
    # For now, we'll set up logging after app instantiation.

    # Ensure TUI's own logging (if any internal to Textual) doesn't conflict.
    # Textual has its own log handling; our setup is for supsrc's structured logs.

    try:
        # Create an asyncio Event for shutdown signaling if needed by the app
        cli_shutdown_event = asyncio.Event()

        # Instantiate the TUI app
        app = SupsrcTuiApp(
            config_path=config_path,
            cli_shutdown_event=cli_shutdown_event
            # Add other necessary parameters here if SupsrcTuiApp constructor requires them
        )

        # --- Logging Setup for TUI ---
        # Set TUI mode active for the logger
        telemetry_logger_base._is_tui_active = True

        # Determine effective file_only_logs for TUI mode.
        effective_file_only_logs: bool
        if file_only_logs is not None: # Check flag from @logging_options
            effective_file_only_logs = file_only_logs
        else:
            # Default to True if a log file is specified (either globally or locally for tui command)
            active_log_file = log_file or ctx.obj.get("LOG_FILE")
            effective_file_only_logs = bool(active_log_file)

        # Setup logging, now passing the TUI app instance
        setup_logging_from_context(
            ctx,
            local_log_level=log_level,
            local_log_file=log_file,
            local_json_logs=json_logs,
            local_file_only_logs=effective_file_only_logs, # Use the determined value
            default_log_level="INFO", # Default for TUI operations
            tui_app_instance=app # Pass the TUI app instance
        )

        log.info("TUI command invoked and logging configured", config_path=str(config_path))

        # Run the TUI app
        app.run()
        log.info("TUI finished.")

    except Exception as e:
        log.error("Failed to launch or run TUI", error=str(e), exc_info=True)
        # Ensure a clean exit code on error
        sys.exit(1)

if __name__ == '__main__':
    # This allows running `python -m src.supsrc.cli.tui_cmds` for testing this command directly
    # For actual use, it's registered with the main CLI group.
    # A minimal context object for standalone testing:
    class MinimalContext:
        obj = {"LOG_LEVEL": "INFO", "LOG_FILE": None, "JSON_LOGS": False}

    minimal_ctx = MinimalContext()

    # Example direct call (requires a dummy config):
    # Create a dummy config for direct testing if needed
    # Path("dummy_supsrc.conf").write_text("# Dummy config\n")
    # tui_cli.main(args=['--config', 'dummy_supsrc.conf'], standalone_mode=False, ctx=minimal_ctx)

    click.echo("To test this command, ensure you have a valid supsrc config file.")
    click.echo("Example: python -m src.supsrc.cli.tui_cmds --config examples/supsrc.conf")

# 🖥️✨

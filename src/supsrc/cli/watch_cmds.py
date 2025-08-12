#
# supsrc/cli/watch_cmds.py
#

import asyncio
import signal
from pathlib import Path

import click
import structlog

# --- Rich Imports ---
# Import logging utilities
from supsrc.cli.utils import logging_options, setup_logging_from_context

# Use absolute imports
from supsrc.telemetry import StructLogger

# --- Try importing TUI App Class ---
# (TUI import logic remains the same)
try:
    from supsrc.tui.app import SupsrcTuiApp

    TEXTUAL_AVAILABLE = True
    log_tui = structlog.get_logger("cli.watch.tui_check")
    log_tui.debug("Successfully imported supsrc.tui.app.SupsrcTuiApp.")
except ImportError as e:
    TEXTUAL_AVAILABLE = False
    SupsrcTuiApp = None
    log_tui = structlog.get_logger("cli.watch.tui_check")
    log_tui.debug(
        "Failed to import supsrc.tui.app. Possible missing 'supsrc[tui]' install or error in tui module.",
        error=str(e),
    )


log: StructLogger = structlog.get_logger("cli.watch")

# --- Global Shutdown Event & Signal Handler (remains the same) ---
_shutdown_requested = asyncio.Event()


async def _handle_signal_async(sig: int):
    # (Implementation remains the same)
    signame = signal.Signals(sig).name
    base_log = structlog.get_logger("cli.watch.signal")
    base_log.warning("Received shutdown signal", signal=signame, signal_num=sig)
    if not _shutdown_requested.is_set():
        base_log.info("Setting shutdown requested event.")
        _shutdown_requested.set()
    else:
        base_log.warning("Shutdown already requested, signal ignored.")


# --- Click Command Definition (remains the same) ---
@click.command(name="watch")
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
def watch_cli(ctx: click.Context, config_path: Path, **kwargs):
    """Interactive dashboard for monitoring repositories."""
    # Setup logging for TUI mode
    ctx.obj.get("LOG_FILE")  # Check if global --log-file was set

    # For TUI mode, always default to file_only_logs=True to prevent console log pollution
    effective_file_only_logs = kwargs.get("file_only_logs")
    if effective_file_only_logs is None:
        effective_file_only_logs = True  # Always suppress console logs in TUI mode

    setup_logging_from_context(
        ctx,
        local_log_level=kwargs.get("log_level"),
        local_log_file=kwargs.get("log_file"),
        local_json_logs=kwargs.get("json_logs"),
        local_file_only_logs=effective_file_only_logs,
    )

    # Always run in TUI mode
    if not TEXTUAL_AVAILABLE or SupsrcTuiApp is None:
        click.echo("Error: watch command requires 'supsrc[tui]' to be installed.", err=True)
        click.echo("Hint: pip install 'supsrc[tui]' or uv tool install -e '.[tui]'", err=True)
        ctx.exit(1)

    log.info("Initializing interactive dashboard...")
    app = SupsrcTuiApp(config_path=config_path, cli_shutdown_event=_shutdown_requested)
    
    try:
        app.run()
        log.info("Interactive dashboard finished.")
    except KeyboardInterrupt:
        log.info("Keyboard interrupt received")
        # Force exit on interrupt
        import os
        os._exit(0)
    except Exception as e:
        log.error(f"TUI error: {e}")
        # Only restore terminal on crash
        import os
        try:
            os.system('stty sane')
        except Exception:
            pass
        # Force exit on error
        os._exit(1)
    
    # This should never be reached due to os._exit in action_quit
    # but just in case...
    import os
    os._exit(0)


# 🔼⚙️

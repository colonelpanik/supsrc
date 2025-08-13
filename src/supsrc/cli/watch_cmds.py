# src/supsrc/cli/watch_cmds.py

import asyncio
import signal
from pathlib import Path

import click
import structlog

from supsrc.cli.utils import logging_options, setup_logging_from_context
from supsrc.telemetry import StructLogger

try:
    from supsrc.tui.app import SupsrcTuiApp
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False
    SupsrcTuiApp = None

log: StructLogger = structlog.get_logger("cli.watch")

_shutdown_requested = asyncio.Event()

async def _handle_signal_async(sig: int):
    signame = signal.Signals(sig).name
    base_log = structlog.get_logger("cli.watch.signal")
    base_log.warning("Received shutdown signal", signal=signame, signal_num=sig)
    if not _shutdown_requested.is_set():
        base_log.info("Setting shutdown requested event.")
        _shutdown_requested.set()
    else:
        base_log.warning("Shutdown already requested, signal ignored.")

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
    # Step 1: Check for TUI dependencies before configuring logging.
    if not TEXTUAL_AVAILABLE or SupsrcTuiApp is None:
        # Set up basic console logging to ensure the error is visible.
        setup_logging_from_context(ctx)
        log.error("TUI dependencies not installed for 'watch' command.")
        click.echo(
            "Error: The 'watch' command requires the 'textual' library, provided by the 'tui' extra.",
            err=True,
        )
        click.echo("Hint: pip install 'supsrc[tui]' or uv pip install 'supsrc[tui]'", err=True)
        ctx.exit(1)
        return

    # Step 2: Dependencies are available. Now run the TUI application.
    # The TUI app itself will configure the final logging setup.
    log.info("Initializing interactive dashboard...")

    try:
        app = SupsrcTuiApp(config_path=config_path, cli_shutdown_event=_shutdown_requested)
        # The app's on_mount will configure logging with the Textual handler.
        app.run()
        log.info("Interactive dashboard finished.")
    except KeyboardInterrupt:
        log.warning("Shutdown requested via KeyboardInterrupt during TUI run.")
        click.echo("\nAborted!")
        ctx.exit(1)
    except Exception as e:
        log.critical("The TUI application crashed unexpectedly.", error=str(e), exc_info=True)
        click.echo(f"\nAn unexpected error occurred in the TUI: {e}", err=True)
        ctx.exit(1)

# 🔼⚙️

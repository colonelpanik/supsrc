# src/supsrc/cli/tail_cmds.py

import asyncio
import logging
import sys
from pathlib import Path

import click
import structlog

from supsrc.cli.utils import logging_options, setup_logging_from_context
from supsrc.runtime.orchestrator import WatchOrchestrator
from supsrc.telemetry import StructLogger

log: StructLogger = structlog.get_logger("cli.tail")


def _run_headless_orchestrator(orchestrator: WatchOrchestrator) -> int:
    """
    Runs the orchestrator using the standard asyncio.run(), which provides
    robust signal handling and lifecycle management.
    """
    try:
        # asyncio.run() is the preferred, high-level way to run an async application.
        # It creates a new event loop, runs the coroutine until it completes,
        # and handles cleanup. Crucially, it also adds its own signal handlers
        # for SIGINT and SIGTERM that will correctly cancel the main task.
        asyncio.run(orchestrator.run())
        return 0
    except KeyboardInterrupt:
        # This block is entered when CTRL-C is pressed.
        # The finally block within orchestrator.run() will have already been
        # executed by the time we get here, due to the task cancellation
        # handled by asyncio.run().
        log.warning("Shutdown initiated by KeyboardInterrupt (CTRL-C).")
        return 130  # Standard exit code for SIGINT
    except Exception:
        # This catches any other unhandled exceptions from the orchestrator.
        log.critical("Orchestrator exited with an unhandled exception.", exc_info=True)
        return 1
    finally:
        # Final log message after the event loop is closed.
        logging.shutdown()


@click.command(name="tail")
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
def tail_cli(ctx: click.Context, config_path: Path, **kwargs):
    """Follow repository changes and trigger actions (non-interactive mode)."""
    # The shutdown event is still necessary to signal between async components.
    # asyncio.run() will manage propagating the initial cancellation.
    shutdown_event = asyncio.Event()

    setup_logging_from_context(
        ctx,
        local_log_level=kwargs.get("log_level"),
        local_log_file=kwargs.get("log_file"),
        local_json_logs=kwargs.get("json_logs"),
        headless_mode=True,
    )

    log.info("Initializing tail command...")

    orchestrator = WatchOrchestrator(
        config_path=config_path,
        shutdown_event=shutdown_event,
        app=None,  # No TUI
        console=None,
    )

    exit_code = _run_headless_orchestrator(orchestrator)

    log.info("'tail' command finished.")
    if exit_code != 0:
        sys.exit(exit_code)
        
# 🔼⚙️

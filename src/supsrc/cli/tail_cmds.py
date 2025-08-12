#
# supsrc/cli/tail_cmds.py
#
"""
Tail command for non-interactive repository monitoring.
This is the headless version of the old 'watch' command.
"""

import asyncio
import logging
import signal
import sys
from contextlib import suppress
from pathlib import Path

import click
import structlog

# Import logging utilities
from supsrc.cli.utils import logging_options, setup_logging_from_context
from supsrc.runtime.orchestrator import WatchOrchestrator

# Use absolute imports
from supsrc.telemetry import StructLogger

log: StructLogger = structlog.get_logger("cli.tail")

# --- Global Shutdown Event & Signal Handler ---
_shutdown_requested = asyncio.Event()


async def _handle_signal_async(sig: int):
    """Handle shutdown signals asynchronously."""
    signame = signal.Signals(sig).name
    base_log = structlog.get_logger("cli.tail.signal")
    base_log.warning("Received shutdown signal", signal=signame, signal_num=sig)
    if not _shutdown_requested.is_set():
        base_log.info("Setting shutdown requested event.")
        _shutdown_requested.set()
    else:
        base_log.warning("Shutdown already requested, signal ignored.")


def _run_headless_orchestrator(orchestrator: WatchOrchestrator) -> int:
    """
    Sets up the asyncio loop and signal handlers to run the orchestrator
    in a headless (non-TUI) mode.

    Returns:
        An integer exit code.
    """
    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    signals_to_handle = (signal.SIGINT, signal.SIGTERM)
    handlers_added = False
    log.debug(f"Adding signal handlers to loop {id(loop)}")
    try:
        for sig in signals_to_handle:
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(_handle_signal_async(s)))
        handlers_added = True
        log.debug("Added signal handlers")
    except Exception as e:
        log.error("Failed to add signal handlers", error=str(e), exc_info=True)

    exit_code = 0
    main_task: asyncio.Task | None = None

    try:
        log.debug("Creating main orchestrator task...")
        main_task = loop.create_task(orchestrator.run(), name="OrchestratorRun")
        log.debug(f"Running event loop {id(loop)}...")
        loop.run_until_complete(main_task)
        log.debug("Orchestrator task completed normally.")
    except KeyboardInterrupt:
        log.warning("KeyboardInterrupt caught. Signalling shutdown.")
        if not _shutdown_requested.is_set():
            _shutdown_requested.set()
        exit_code = 130
    except asyncio.CancelledError:
        log.warning("Main orchestrator task cancelled.")
        if not _shutdown_requested.is_set():
            _shutdown_requested.set()
        exit_code = 1
    except Exception as e:
        log.critical("Orchestrator run failed", error=str(e), exc_info=True)
        if not _shutdown_requested.is_set():
            _shutdown_requested.set()
        exit_code = 1
    finally:
        log.debug(f"tail_cli finally block starting. Loop closed: {loop.is_closed()}")

        if not loop.is_closed():
            # ... (Graceful Task Cleanup as before) ...
            if handlers_added:
                log.debug("Removing signal handlers")
                for sig in signals_to_handle:
                    with suppress(ValueError, RuntimeError, Exception):
                        loop.remove_signal_handler(sig)

            log.debug("Shutting down async generators...")
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            log.info("Event loop closed.")

        log.debug("Shutting down standard logging...")
        logging.shutdown()

    return exit_code


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
    # 1. Setup logging (this is a CLI concern)
    setup_logging_from_context(
        ctx,
        local_log_level=kwargs.get("log_level"),
        local_log_file=kwargs.get("log_file"),
        local_json_logs=kwargs.get("json_logs"),
        local_file_only_logs=kwargs.get("file_only_logs", False),
    )

    log.info("Initializing tail command...")

    # 2. Instantiate the application logic object
    orchestrator = WatchOrchestrator(
        config_path=config_path,
        shutdown_event=_shutdown_requested,
        app=None,  # No TUI app
        console=None,  # No Rich console for this mode
    )

    # 3. Hand off to the runner and get the exit code
    exit_code = _run_headless_orchestrator(orchestrator)

    log.info("'tail' command finished.")
    if exit_code != 0:
        sys.exit(exit_code)


# 🔼⚙️

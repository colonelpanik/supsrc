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
    # Setup logging for this command
    setup_logging_from_context(
        ctx,
        local_log_level=kwargs.get("log_level"),
        local_log_file=kwargs.get("log_file"),
        local_json_logs=kwargs.get("json_logs"),
        local_file_only_logs=kwargs.get("file_only_logs", False),
    )

    # --- Standard Mode Logic (from old watch command) ---
    # Don't use Rich console to avoid terminal control issues
    print("INFO: Initializing tail command...")

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

    orchestrator = WatchOrchestrator(
        config_path=config_path,
        shutdown_event=_shutdown_requested,
        app=None,
        console=None,  # Don't pass console to avoid Rich terminal handling
    )
    exit_code = 0
    main_task: asyncio.Task | None = None

    try:
        # Don't use console.screen() as it interferes with signal handling
        log.debug("Creating main orchestrator task...")
        main_task = loop.create_task(orchestrator.run(), name="OrchestratorRun")
        log.debug(f"Running event loop {id(loop)}...")
        loop.run_until_complete(main_task)
        log.debug("Orchestrator task completed normally.")
    except KeyboardInterrupt:
        print("\nKEYBOARD INTERRUPT: Signal received. Initiating graceful shutdown...")
        log.warning("KeyboardInterrupt caught. Signalling shutdown.")
        _shutdown_requested.set()
        exit_code = 130
    except asyncio.CancelledError:
        log.warning("Main orchestrator task cancelled.")
        _shutdown_requested.set()
        exit_code = 1
    except Exception as e:
        log.critical("Orchestrator run failed", error=str(e), exc_info=True)
        print(f"CRITICAL: Orchestrator run failed: {e}")
        _shutdown_requested.set()
        exit_code = 1
    finally:
        log.debug(f"tail_cli finally block starting. Loop closed: {loop.is_closed()}")

        # --- Graceful Task Cleanup ---
        if not loop.is_closed():
            try:
                # Ensure main task cancellation propagates if needed
                if main_task and not main_task.done():
                    log.debug("Waiting briefly for main task cancellation...")
                    main_task.cancel()
                    with suppress(asyncio.CancelledError, asyncio.TimeoutError):
                        loop.run_until_complete(asyncio.wait_for(main_task, timeout=1.0))

                # Gather all other remaining tasks
                tasks = asyncio.all_tasks(loop=loop)  # type: ignore[var-annotated]
                current_task = asyncio.current_task(loop=loop)
                tasks_to_wait_for = {
                    t
                    for t in tasks
                    if t is not current_task and t is not main_task and not t.done()
                }  # type: ignore[var-annotated]

                if tasks_to_wait_for:
                    log.debug(
                        f"Gathering results for {len(tasks_to_wait_for)} remaining background tasks...",
                        task_names=[t.get_name() for t in tasks_to_wait_for],
                    )
                    for task in tasks_to_wait_for:
                        if not task.cancelled():
                            task.cancel()
                    loop.run_until_complete(
                        asyncio.gather(*tasks_to_wait_for, return_exceptions=True)
                    )
                    log.debug("Remaining background tasks gathered after potential cancellation.")
                else:
                    log.debug("No remaining background tasks needed gathering.")
            except Exception as task_cleanup_exc:
                log.error(
                    "Error during final task gathering/cleanup",
                    error=str(task_cleanup_exc),
                )

        # --- Cleanup ---
        if handlers_added and not loop.is_closed():
            log.debug("Removing signal handlers")
            for sig in signals_to_handle:
                with suppress(ValueError, RuntimeError, Exception):
                    loop.remove_signal_handler(sig)
                    log.debug(f"Removed signal handler for {signal.Signals(sig).name}")

        log.debug("Shutting down standard logging...")
        with suppress(Exception):
            logging.shutdown()

        log.debug(f"Closing event loop {id(loop)}")
        if not loop.is_closed():
            try:
                # Shutdown async generators FIRST
                loop.run_until_complete(loop.shutdown_asyncgens())
                log.debug("Async generators shut down.")
                # THEN close the loop
                loop.close()
                log.info("Event loop closed.")
            except RuntimeError as e:
                if "cannot schedule new futures after shutdown" in str(e):
                    log.warning(
                        "Loop shutdown encountered scheduling issue, likely benign after cleanup."
                    )
                else:
                    log.error(
                        "Error during final event loop close",
                        error=str(e),
                        exc_info=True,
                    )
            except Exception as e:
                log.error("Error during final event loop close", error=str(e), exc_info=True)
        else:
            log.warning("Event loop was already closed before final cleanup.")

    print("INFO: 'tail' command finished.")
    if exit_code != 0:
        sys.exit(exit_code)


# 🔼⚙️

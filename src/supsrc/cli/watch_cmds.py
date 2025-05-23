# file: src/supsrc/cli/watch_cmds.py

# file: src/supsrc/cli/watch_cmds.py

import asyncio
import logging
import signal
# --- Rich Imports ---
from rich.console import Console
import sys
from contextlib import suppress
from pathlib import Path

import click
import structlog

from supsrc.runtime.orchestrator import WatchOrchestrator

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
    log_tui.debug("Failed to import supsrc.tui.app. Possible missing 'supsrc[tui]' install or error in tui module.", error=str(e))


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
    "-c", "--config-path",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, readable=True, path_type=Path),
    default=Path("supsrc.conf"),
    show_default=True, envvar="SUPSRC_CONF",
    help="Path to the supsrc configuration file (env var SUPSRC_CONF).", show_envvar=True,
)
@click.option(
    "--tui", is_flag=True, default=False,
    help="Run with an interactive Text User Interface (requires 'supsrc[tui]')."
)
@click.pass_context
def watch_cli(ctx: click.Context, config_path: Path, tui: bool):
    """Monitor configured repositories for changes and trigger actions."""
    # def _cli_safe_log(level: str, msg: str, **kwargs): # Replaced with direct console prints or structlog
    #     with suppress(Exception): getattr(log, level)(msg, **kwargs)

    if tui:
        # (TUI logic remains the same)
        if not TEXTUAL_AVAILABLE or SupsrcTuiApp is None:
            click.echo("Error: TUI mode requires 'supsrc[tui]' to be installed and importable.", err=True)
            click.echo("Hint: pip install 'supsrc[tui]' or check for errors in src/supsrc/tui/app.py", err=True)
            ctx.exit(1)
        log.info("Initializing TUI mode...")
        app = SupsrcTuiApp(config_path=config_path, cli_shutdown_event=_shutdown_requested)
        app.run()
        log.info("TUI application finished.")

    else:
        # --- Standard Mode Logic ---
        console = Console() # Create Rich Console instance
        # _cli_safe_log("info", "Initializing standard 'watch' command (non-TUI)")
        console.print("[dim]INFO:[/] Initializing standard 'watch' command (non-TUI)...")
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
            if loop.is_closed(): loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        except RuntimeError: loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)

        signals_to_handle = (signal.SIGINT, signal.SIGTERM); handlers_added = False
        log.debug(f"Adding signal handlers to loop {id(loop)}")
        try:
            for sig in signals_to_handle: loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(_handle_signal_async(s))); handlers_added = True
            log.debug("Added signal handlers")
        except Exception as e:
            # _cli_safe_log("error", "Failed to add signal handlers", error=str(e), exc_info=True)
            log.error("Failed to add signal handlers", error=str(e), exc_info=True) # Use structlog here

        orchestrator = WatchOrchestrator(config_path=config_path, shutdown_event=_shutdown_requested, app=None, console=console)
        exit_code = 0
        main_task: asyncio.Task | None = None
        try:
            # _cli_safe_log("debug", "Creating main orchestrator task...")
            log.debug("Creating main orchestrator task...") # Use structlog
            main_task = loop.create_task(orchestrator.run(), name="OrchestratorRun")
            # _cli_safe_log("debug", f"Running event loop {id(loop)}...")
            log.debug(f"Running event loop {id(loop)}...") # Use structlog
            loop.run_until_complete(main_task)
            # _cli_safe_log("debug", "Orchestrator task completed normally.")
            log.debug("Orchestrator task completed normally.") # Use structlog
        except KeyboardInterrupt:
            # _cli_safe_log("warning", "KeyboardInterrupt caught. Signalling shutdown.")
            console.print("[bold yellow]KEYBOARD INTERRUPT:[/] Signal received. Initiating graceful shutdown...", highlight=False)
            log.warning("KeyboardInterrupt caught. Signalling shutdown.") # Use structlog
            _shutdown_requested.set()
            exit_code = 130
        except asyncio.CancelledError:
            # _cli_safe_log("warning", "Main orchestrator task cancelled.")
            log.warning("Main orchestrator task cancelled.") # Use structlog
            _shutdown_requested.set()
            exit_code = 1
        except Exception as e:
            # _cli_safe_log("critical", "Orchestrator run failed", error=str(e), exc_info=True)
            log.critical("Orchestrator run failed", error=str(e), exc_info=True) # Use structlog
            console.print(f"[bold red]CRITICAL:[/] Orchestrator run failed: {e}", highlight=False)
            _shutdown_requested.set()
            exit_code = 1
        finally:
            # _cli_safe_log("debug", f"watch_cli (non-TUI) finally block starting. Loop closed: {loop.is_closed()}")
            log.debug(f"watch_cli (non-TUI) finally block starting. Loop closed: {loop.is_closed()}")

            # --- FIX: Graceful Task Cleanup using loop.run_until_complete ---
            if not loop.is_closed():
                try:
                    # Ensure main task cancellation propagates if needed
                    if main_task and not main_task.done():
                        # _cli_safe_log("debug", "Waiting briefly for main task cancellation...")
                        log.debug("Waiting briefly for main task cancellation...")
                        main_task.cancel() # Explicitly cancel if not done
                        with suppress(asyncio.CancelledError, asyncio.TimeoutError):
                             # Run within loop until complete
                             loop.run_until_complete(asyncio.wait_for(main_task, timeout=1.0))

                    # Gather all *other* remaining tasks
                    tasks = asyncio.all_tasks(loop=loop) # type: ignore[var-annotated]
                    current_task = asyncio.current_task(loop=loop) # May be None if outside run_until_complete
                    tasks_to_wait_for = {t for t in tasks if t is not current_task and t is not main_task and not t.done()} # type: ignore[var-annotated]

                    if tasks_to_wait_for:
                        # _cli_safe_log("debug", f"Gathering results for {len(tasks_to_wait_for)} remaining background tasks...",
                        #               task_names=[t.get_name() for t in tasks_to_wait_for])
                        log.debug(f"Gathering results for {len(tasks_to_wait_for)} remaining background tasks...",
                                      task_names=[t.get_name() for t in tasks_to_wait_for])
                        for task in tasks_to_wait_for:
                            if not task.cancelled():
                                task.cancel()
                        # Use loop.run_until_complete to run the final gather
                        loop.run_until_complete(
                            asyncio.gather(*tasks_to_wait_for, return_exceptions=True)
                        )
                        # _cli_safe_log("debug", "Remaining background tasks gathered after potential cancellation.")
                        log.debug("Remaining background tasks gathered after potential cancellation.")
                    else:
                        # _cli_safe_log("debug", "No remaining background tasks needed gathering.")
                        log.debug("No remaining background tasks needed gathering.")
                except Exception as task_cleanup_exc:
                    # _cli_safe_log("error", "Error during final task gathering/cleanup", error=str(task_cleanup_exc))
                    log.error("Error during final task gathering/cleanup", error=str(task_cleanup_exc))
            # --- End FIX ---

            # --- Existing Cleanup ---
            if handlers_added and not loop.is_closed():
                 # _cli_safe_log("debug", "Removing signal handlers")
                 log.debug("Removing signal handlers")
                 for sig in signals_to_handle:
                      with suppress(ValueError, RuntimeError, Exception):
                           loop.remove_signal_handler(sig)
                           # _cli_safe_log("debug", f"Removed signal handler for {signal.Signals(sig).name}")
                           log.debug(f"Removed signal handler for {signal.Signals(sig).name}")

            # _cli_safe_log("debug", "Shutting down standard logging...")
            log.debug("Shutting down standard logging...")
            with suppress(Exception): logging.shutdown()

            # _cli_safe_log("debug", f"Closing event loop {id(loop)}")
            log.debug(f"Closing event loop {id(loop)}")
            if not loop.is_closed():
                try:
                    # Shutdown async generators FIRST
                    loop.run_until_complete(loop.shutdown_asyncgens())
                    # _cli_safe_log("debug", "Async generators shut down.")
                    log.debug("Async generators shut down.")
                    # THEN close the loop
                    loop.close()
                    # _cli_safe_log("info", "Event loop closed.")
                    log.info("Event loop closed.")
                except RuntimeError as e:
                    if "cannot schedule new futures after shutdown" in str(e):
                        # _cli_safe_log("warning", "Loop shutdown encountered scheduling issue, likely benign after cleanup.")
                        log.warning("Loop shutdown encountered scheduling issue, likely benign after cleanup.")
                    else:
                        # _cli_safe_log("error", "Error during final event loop close", error=str(e), exc_info=True)
                        log.error("Error during final event loop close", error=str(e), exc_info=True)
                except Exception as e:
                     # _cli_safe_log("error", "Error during final event loop close", error=str(e), exc_info=True)
                     log.error("Error during final event loop close", error=str(e), exc_info=True)
            else:
                 # _cli_safe_log("warning", "Event loop was already closed before final cleanup.")
                 log.warning("Event loop was already closed before final cleanup.")

        # _cli_safe_log("info", "'watch' command finished (non-TUI mode).")
        console.print("[dim]INFO:[/] 'watch' command finished.")
        if exit_code != 0:
            sys.exit(exit_code)

# 🔼⚙️

#
# supsrc/monitor/service.py
#

import asyncio

import structlog
from watchdog.observers import Observer

# Use absolute imports
from supsrc.config.models import RepositoryConfig
from supsrc.exceptions import MonitoringError, MonitoringSetupError
from supsrc.monitor.events import MonitoredEvent
from supsrc.monitor.handler import SupsrcEventHandler

log = structlog.get_logger("monitor.service")


class MonitoringService:
    """
    Manages the filesystem monitoring using watchdog.

    Creates and manages event handlers for each repository and runs the
    watchdog observer in a separate thread.
    """

    def __init__(self, event_queue: asyncio.Queue[MonitoredEvent]):
        """
        Initializes the MonitoringService.

        Args:
            event_queue: The asyncio Queue where filtered events will be placed.
        """
        self._event_queue = event_queue
        self._observer = Observer()
        self._handlers: dict[str, SupsrcEventHandler] = {}
        self._logger = log
        self._is_running = False
        log.debug("MonitoringService initialized")


    def add_repository(
        self, repo_id: str, repo_config: RepositoryConfig, loop: asyncio.AbstractEventLoop # <<< Added loop
    ) -> None:
        """ Adds a repository to be monitored. """
        if not repo_config.enabled or not repo_config._path_valid:
            self._logger.warning(
                "Skipping disabled or invalid repository", repo_id=repo_id, path=str(repo_config.path),
                enabled=repo_config.enabled, path_valid=repo_config._path_valid,
            )
            return
        repo_path = repo_config.path
        if not repo_path.is_dir():
            raise MonitoringSetupError("Repository path is not a valid directory", repo_id=repo_id, path=str(repo_path))

        self._logger.info("Adding repository to monitor", repo_id=repo_id, path=str(repo_path))
        # --- FIX: Pass the loop to the handler ---
        handler = SupsrcEventHandler(
            repo_id=repo_id,
            repo_path=repo_path,
            event_queue=self._event_queue,
            loop=loop # Pass the main event loop
        )
        # ---------------------------------------
        self._handlers[repo_id] = handler
        try:
            self._observer.schedule(handler, str(repo_path), recursive=True)
            self._logger.debug("Scheduled handler with observer", repo_id=repo_id)
        except Exception as e:
            self._logger.error(
                "Failed to schedule monitoring for repository", repo_id=repo_id, path=str(repo_path), error=str(e), exc_info=True
            )
            if repo_id in self._handlers: del self._handlers[repo_id]
            raise MonitoringSetupError(f"Failed to schedule monitoring: {e}", repo_id=repo_id, path=str(repo_path)) from e


    def start(self) -> None:
        """Starts the watchdog observer thread."""
        # (Implementation remains the same)
        if not self._handlers:
             self._logger.warning("No repositories configured or added for monitoring. Observer not started.")
             return
        if self._is_running:
            self._logger.warning("Monitoring service already running.")
            return
        try:
            log.debug("Calling observer.start()")
            self._observer.start()
            self._is_running = True
            self._logger.info("Monitoring service started", num_handlers=len(self._handlers))
            log.debug("observer.start() finished")
        except Exception as e:
            self._logger.critical("Failed to start monitoring observer", error=str(e), exc_info=True)
            raise MonitoringError(f"Failed to start observer thread: {e}") from e


    async def stop(self) -> None:
        """Stops the watchdog observer thread gracefully."""
        # (Implementation remains the same)
        if not self._is_running:
            self._logger.info("Monitoring service already stopped.")
            return
        self._logger.info("Stopping monitoring service...")
        thread_stopped = False
        join_success = False
        try:
            log.debug("Calling observer.stop()")
            self._observer.stop()
            log.debug("observer.stop() returned")
            self._logger.debug("Waiting for observer thread to join via asyncio.to_thread with overall timeout...")
            try:
                # Wrap the asyncio.to_thread call with asyncio.wait_for
                await asyncio.wait_for(
                    asyncio.to_thread(self._observer.join, timeout=5.0),
                    timeout=7.0  # Outer timeout for the to_thread operation itself
                )
                join_success = True
                log.debug("asyncio.to_thread(observer.join) completed within outer timeout.")
            except asyncio.TimeoutError:
                log.error("Outer timeout (7s) reached while waiting for observer.join via asyncio.to_thread.", exc_info=True)
                # join_success remains False
            except Exception as join_exc:
                log.error("Exception during observer join or outer wait_for", error=str(join_exc), exc_info=True)
                # join_success remains False
            if self._observer.is_alive():
                 self._logger.warning("Observer thread did not stop within timeout or failed join.")
            else:
                 if join_success:
                     thread_stopped = True
                     self._logger.info("Observer thread stopped.")
                 else:
                      self._logger.warning("Observer thread stopped but join failed.")
        except Exception as e:
            self._logger.error("Error stopping monitoring observer", error=str(e), exc_info=True)
        finally:
             self._is_running = False
             if thread_stopped:
                self._logger.info("Monitoring service cleanup successful.")
             else:
                 self._logger.warning("Monitoring service stopped, but observer thread join may have failed or timed out.")


    @property
    def is_running(self) -> bool:
        """Returns True if the observer thread is currently active."""
        # (Implementation remains the same)
        observer_alive = hasattr(self, "_observer") and self._observer is not None and self._observer.is_alive()
        return self._is_running and observer_alive

# 🔼⚙️


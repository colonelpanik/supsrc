# src/supsrc/monitor/service.py

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

    def clear_handlers(self) -> None:
        """Clears all internal handler references. Note: Watchdog's Observer.stop() handles unscheduling from the observer itself."""
        self._handlers.clear()
        self._logger.debug("Cleared all monitoring handlers.")

    def unschedule_repository(self, repo_id: str) -> None:
        """Unschedule a specific repository from being monitored."""
        handler = self._handlers.pop(repo_id, None)
        if handler:
            self._observer.unschedule(handler)
            self._logger.info("Unscheduled repository from monitoring", repo_id=repo_id)
        else:
            self._logger.warning("Attempted to unschedule non-existent handler", repo_id=repo_id)

    def add_repository(
        self,
        repo_id: str,
        repo_config: RepositoryConfig,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Adds a repository to be monitored."""
        if not repo_config.enabled or not repo_config._path_valid:
            self._logger.warning(
                "Skipping disabled or invalid repository",
                repo_id=repo_id,
                path=str(repo_config.path),
                enabled=repo_config.enabled,
                path_valid=repo_config._path_valid,
            )
            return
        repo_path = repo_config.path
        if not repo_path.is_dir():
            raise MonitoringSetupError(
                "Repository path is not a valid directory",
                repo_id=repo_id,
                path=str(repo_path),
            )

        self._logger.info("Adding repository to monitor", repo_id=repo_id, path=str(repo_path))
        handler = SupsrcEventHandler(
            repo_id=repo_id,
            repo_path=repo_path,
            event_queue=self._event_queue,
            loop=loop,
        )
        self._handlers[repo_id] = handler
        try:
            self._observer.schedule(handler, str(repo_path), recursive=True)
            self._logger.debug("Scheduled handler with observer", repo_id=repo_id)
        except Exception as e:
            self._logger.error(
                "Failed to schedule monitoring for repository",
                repo_id=repo_id,
                path=str(repo_path),
                error=str(e),
                exc_info=True,
            )
            if repo_id in self._handlers:
                del self._handlers[repo_id]
            raise MonitoringSetupError(
                f"Failed to schedule monitoring: {e}",
                repo_id=repo_id,
                path=str(repo_path),
            ) from e

    def start(self) -> None:
        """Starts the watchdog observer thread."""
        if not self._handlers:
            self._logger.warning(
                "No repositories configured or added for monitoring. Observer not started."
            )
            return
        if self._is_running:
            self._logger.warning("Monitoring service already running.")
            return
        try:
            log.debug("Calling observer.start()")
            # Make the observer thread a daemon so it doesn't block program exit
            self._observer.daemon = True
            self._observer.start()
            self._is_running = True
            self._logger.info("Monitoring service started", num_handlers=len(self._handlers))
            log.debug("observer.start() finished")
        except Exception as e:
            self._logger.critical(
                "Failed to start monitoring observer", error=str(e), exc_info=True
            )
            raise MonitoringError(f"Failed to start observer thread: {e}") from e

    async def stop(self) -> None:
        """
        Signals the watchdog observer thread to stop and waits for it to join
        without blocking the asyncio event loop.
        """
        if not self._is_running or not self._observer.is_alive():
            self._logger.info("Monitoring service already stopped or not running.")
            self._is_running = False
            return

        self._logger.info("Stopping monitoring service...")

        def _blocking_shutdown():
            """The blocking part of the shutdown to be run in a thread."""
            if not self._observer.is_alive():
                return
            log.debug("Signaling watchdog observer to stop...")
            self._observer.stop()
            log.debug("Waiting for watchdog observer to join...")
            self._observer.join(timeout=2.0)
            if self._observer.is_alive():
                log.warning("Watchdog observer thread did not join within timeout.")

        try:
            # Run the blocking shutdown sequence in a separate thread
            await asyncio.to_thread(_blocking_shutdown)
            self._logger.info("Monitoring service shutdown complete.")
        except Exception as e:
            self._logger.error("Error during monitoring service shutdown", error=str(e), exc_info=True)
        finally:
            self._is_running = False

    @property
    def is_running(self) -> bool:
        """Returns True if the observer thread is currently active."""
        observer_alive = (
            hasattr(self, "_observer") and self._observer is not None and self._observer.is_alive()
        )
        return self._is_running and observer_alive
        
# 🔼⚙️

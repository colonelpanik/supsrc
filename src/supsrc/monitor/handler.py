#
# supsrc/monitor/handler.py
#
"""
Custom watchdog FileSystemEventHandler for supsrc.

Filters events based on .git directory and .gitignore rules, then
puts relevant events onto an asyncio Queue using thread-safe methods.
"""

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pathspec
import structlog
from watchdog.events import FileSystemEvent, FileSystemEventHandler

# Use relative import for the event structure
from .events import MonitoredEvent

# Conditional import for type hinting the loop
if TYPE_CHECKING:
    from asyncio import AbstractEventLoop

log = structlog.get_logger("monitor.handler")

# Define parts of the .git directory to always ignore
# Convert to strings for simple startswith check
GIT_DIR_PARTS = (
    f"{os.sep}.git{os.sep}",
    f"{os.sep}.git",
)


class SupsrcEventHandler(FileSystemEventHandler):
    """
    Handles filesystem events, filters them, and queues them for processing.

    Runs within the watchdog observer thread. Uses loop.call_soon_threadsafe
    for putting items onto the asyncio Queue from the observer thread.
    """

    def __init__(
        self,
        repo_id: str,
        repo_path: Path,
        event_queue: asyncio.Queue[MonitoredEvent],
        loop: "AbstractEventLoop",  # <<< Added loop parameter
    ):
        """
        Initializes the event handler for a specific repository.

        Args:
            repo_id: The unique identifier for the repository.
            repo_path: The absolute Path object to the repository root.
            event_queue: The asyncio Queue to put filtered MonitoredEvent objects onto.
            loop: The asyncio event loop the consumer task is running on.
        """
        super().__init__()
        self.repo_id = repo_id
        self.repo_path = repo_path
        self.event_queue = event_queue
        self.loop = loop  # <<< Store the loop
        self.logger = log.bind(repo_id=repo_id, repo_path=str(repo_path))
        self.gitignore_spec: pathspec.PathSpec | None = self._load_gitignore()

        self.logger.debug("Initialized event handler")

    def _load_gitignore(self) -> pathspec.PathSpec | None:
        """Loads and parses the .gitignore file for the repository."""
        # (Implementation remains the same)
        gitignore_path = self.repo_path / ".gitignore"
        spec = None
        if gitignore_path.is_file():
            try:
                with open(gitignore_path, encoding="utf-8") as f:
                    spec = pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, f)
                self.logger.info("Loaded .gitignore patterns", path=str(gitignore_path))
            except OSError as e:
                self.logger.error(
                    "Failed to read .gitignore file",
                    path=str(gitignore_path),
                    error=str(e),
                )
            except Exception as e:
                self.logger.error(
                    "Failed to parse .gitignore file",
                    path=str(gitignore_path),
                    error=str(e),
                    exc_info=True,
                )
        else:
            self.logger.debug(".gitignore file not found, no ignore patterns loaded.")
        return spec

    def _is_ignored(self, file_path: Path) -> bool:
        """Checks if a given absolute path should be ignored."""
        # (Implementation remains the same)
        norm_path_str = os.path.normpath(str(file_path))
        repo_path_str = os.path.normpath(str(self.repo_path))
        if norm_path_str.startswith(
            os.path.join(repo_path_str, ".git") + os.sep
        ) or norm_path_str == os.path.join(repo_path_str, ".git"):
            self.logger.debug("Ignoring event inside .git directory", path=str(file_path))
            return True

        if self.gitignore_spec:
            try:
                relative_path = file_path.relative_to(self.repo_path)
                if self.gitignore_spec.match_file(str(relative_path)):
                    self.logger.debug("Ignoring event due to .gitignore match", path=str(file_path))
                    return True
            except ValueError:
                self.logger.debug("Event path not relative to repo path, ignoring", path=str(file_path))
                return True  # Ignore paths outside the repo being watched
        return False

    def _queue_event_threadsafe(self, monitored_event: MonitoredEvent):
        """Target function for call_soon_threadsafe to put item in queue."""
        try:
            self.event_queue.put_nowait(monitored_event)
            # Logging from the handler thread might be slightly delayed relative to event processing now
            self.logger.info(
                "Queued filesystem event (via threadsafe)",
                event_type=monitored_event.event_type,
                path=str(monitored_event.src_path),
                is_dir=monitored_event.is_directory,
                dest=str(monitored_event.dest_path) if monitored_event.dest_path else None,
            )
        except asyncio.QueueFull:
            self.logger.error(
                "Event queue is full, discarding event. Consumer might be blocked.",
                event_details=monitored_event,
            )
        except Exception as e:
            self.logger.error(
                "Unexpected error queuing event via threadsafe call",
                error=str(e),
                exc_info=True,
                event_details=monitored_event,
            )

    def _process_and_queue_event(self, event: FileSystemEvent):
        """Processes, filters, and queues a watchdog event using thread-safe mechanism."""
        # (Filtering logic remains the same)
        event_type = event.event_type
        src_path_str = event.src_path
        dest_path_str = getattr(event, "dest_path", None)

        try:
            src_path = Path(src_path_str).resolve()
            dest_path = Path(dest_path_str).resolve() if dest_path_str else None
        except Exception as e:
            self.logger.error(
                "Failed to resolve event path(s)",
                src=src_path_str,
                dest=dest_path_str,
                error=str(e),
            )
            return

        if src_path.name == ".gitignore":
            self.logger.debug("Ignoring .gitignore file event", path=str(src_path))
            return

        if self._is_ignored(src_path):
            return

        if event_type == "moved" and dest_path and self._is_ignored(dest_path):
            self.logger.debug(
                "Ignoring 'moved' event, destination is ignored",
                dest_path=str(dest_path),
            )
            return

        monitored_event = MonitoredEvent(
            repo_id=self.repo_id,
            event_type=event_type,
            src_path=src_path,
            is_directory=event.is_directory,
            dest_path=dest_path,
        )

        # --- FIX: Use loop.call_soon_threadsafe ---
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self._queue_event_threadsafe, monitored_event)
        else:
            # Should not happen if orchestrator is running, but log as warning
            self.logger.warning(
                "Event loop not running, cannot queue event threadsafe.",
                event=monitored_event,
            )
        # ------------------------------------------

    # Override watchdog methods to call the processing function
    def on_created(self, event: FileSystemEvent):
        self._process_and_queue_event(event)

    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory:
            self._process_and_queue_event(event)
        else:
            self.logger.debug("Ignoring directory modification event", path=event.src_path)

    def on_deleted(self, event: FileSystemEvent):
        self._process_and_queue_event(event)

    def on_moved(self, event: FileSystemEvent):
        self._process_and_queue_event(event)


# 🔼⚙️

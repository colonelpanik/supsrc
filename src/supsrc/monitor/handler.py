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

from .events import MonitoredEvent

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop

log = structlog.get_logger("monitor.handler")


# --- NEW: Default ignore patterns to prevent feedback loops ---
DEFAULT_IGNORE_PATTERNS = [
    # Python
    "__pycache__/",
    ".pytest_cache/",
    ".venv/",
    "venv/",
    "env/",
    "*.pyc",
    "*.pyo",
    "*.pyd",

    # Node.js / TypeScript
    "node_modules/",
    "dist/",
    "build/",
    ".next/",
    ".nuxt/",
    "*.tsbuildinfo",
    "npm-debug.log*",
    "yarn-error.log*",
    "yarn.lock", # Often generated but not always committed, can be noisy
    "pnpm-lock.yaml",

    # Go
    "bin/",

    # Rust
    "target/",

    # General / Secrets / Logs
    ".env",
    ".env.local",
    ".env.*",
    "*.log",
]

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
        loop: "AbstractEventLoop",
    ):
        """
        Initializes the event handler for a specific repository.
        """
        super().__init__()
        self.repo_id = repo_id
        self.repo_path = repo_path
        self.event_queue = event_queue
        self.loop = loop
        self.logger = log.bind(repo_id=repo_id, repo_path=str(repo_path))
        
        # --- MODIFIED: Load both default and gitignore specs ---
        self.default_spec = pathspec.PathSpec.from_lines(
            pathspec.patterns.GitWildMatchPattern, DEFAULT_IGNORE_PATTERNS
        )
        self.gitignore_spec: pathspec.PathSpec | None = self._load_gitignore()
        # --- END MODIFICATION ---

        self.logger.debug("Initialized event handler")

    def _load_gitignore(self) -> pathspec.PathSpec | None:
        """Loads and parses the .gitignore file for the repository."""
        gitignore_path = self.repo_path / ".gitignore"
        if gitignore_path.is_file():
            try:
                with open(gitignore_path, encoding="utf-8") as f:
                    # Combine default patterns with user-defined gitignore
                    lines = f.readlines()
                spec = pathspec.PathSpec.from_lines(pathspec.patterns.GitWildMatchPattern, lines)
                self.logger.info("Loaded .gitignore patterns", path=str(gitignore_path))
                return spec
            except Exception as e:
                self.logger.error("Failed to load or parse .gitignore", path=str(gitignore_path), error=str(e))
        return None

    def _is_ignored(self, file_path: Path) -> bool:
        """Checks if a given absolute path should be ignored by any rule."""
        # Always ignore the .git directory itself
        if ".git" in file_path.parts:
            self.logger.debug("Ignoring event inside .git directory", path=str(file_path))
            return True

        try:
            relative_path = file_path.relative_to(self.repo_path)
        except ValueError:
            # Path is not within the repository root, so we should ignore it.
            self.logger.debug("Event path not relative to repo path, ignoring", path=str(file_path))
            return True

        # --- MODIFIED: Check both default spec and .gitignore spec ---
        if self.default_spec.match_file(str(relative_path)):
            self.logger.debug("Ignoring event due to default supsrc ignore match", path=str(file_path))
            return True

        if self.gitignore_spec and self.gitignore_spec.match_file(str(relative_path)):
            self.logger.debug("Ignoring event due to .gitignore match", path=str(file_path))
            return True
        # --- END MODIFICATION ---

        return False

    def _queue_event_threadsafe(self, monitored_event: MonitoredEvent):
        """Target function for call_soon_threadsafe to put item in queue."""
        try:
            self.event_queue.put_nowait(monitored_event)
            self.logger.info(
                "Queued filesystem event (via threadsafe)",
                event_type=monitored_event.event_type,
                path=str(monitored_event.src_path),
            )
        except asyncio.QueueFull:
            self.logger.error("Event queue is full, discarding event.")
        except Exception as e:
            self.logger.error("Unexpected error queuing event", error=str(e), exc_info=True)

    def _process_and_queue_event(self, event: FileSystemEvent):
        """Processes, filters, and queues a watchdog event."""
        # Ignore noisy directory modification events
        if event.is_directory and event.event_type == "modified":
            return

        try:
            src_path = Path(event.src_path).resolve()
        except (FileNotFoundError, RuntimeError):
            # The file might be gone before we can resolve it, especially with temp files.
            self.logger.debug("Could not resolve path for event, likely a transient file.", src_path=event.src_path)
            return

        if self._is_ignored(src_path):
            return

        dest_path = None
        if event.event_type == "moved":
            try:
                dest_path = Path(getattr(event, "dest_path", None)).resolve()
                if self._is_ignored(dest_path):
                    self.logger.debug("Ignoring 'moved' event, destination is ignored", dest_path=str(dest_path))
                    return
            except (FileNotFoundError, RuntimeError):
                 self.logger.debug("Could not resolve moved dest_path", dest_path=getattr(event, "dest_path", None))
                 return # Ignore if destination is gone

        monitored_event = MonitoredEvent(
            repo_id=self.repo_id,
            event_type=event.event_type,
            src_path=src_path,
            is_directory=event.is_directory,
            dest_path=dest_path,
        )

        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self._queue_event_threadsafe, monitored_event)

    def on_created(self, event: FileSystemEvent):
        self._process_and_queue_event(event)

    def on_modified(self, event: FileSystemEvent):
        self._process_and_queue_event(event)

    def on_deleted(self, event: FileSystemEvent):
        self._process_and_queue_event(event)

    def on_moved(self, event: FileSystemEvent):
        self._process_and_queue_event(event)

# 🔼⚙️

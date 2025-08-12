#
# supsrc/monitor/events.py
#
"""
Defines the event structure used for communication between monitor handler and consumer.
"""

from pathlib import Path

from attrs import frozen


@frozen(slots=True)
class MonitoredEvent:
    """Represents a filtered filesystem event from a monitored repository."""

    repo_id: str
    event_type: str  # e.g., 'created', 'modified', 'deleted', 'moved'
    src_path: Path  # Absolute path
    is_directory: bool
    dest_path: Path | None = None  # Absolute path, only for 'moved' events


# 🔼⚙️

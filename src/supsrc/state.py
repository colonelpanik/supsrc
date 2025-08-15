# src/supsrc/state.py
#
"""
Defines the dynamic state management models for monitored repositories in supsrc.
"""

import asyncio
from datetime import UTC, datetime
from enum import Enum, auto

import structlog
from attrs import field, mutable

# Logger specific to state management
log: structlog.stdlib.BoundLogger = structlog.get_logger("state")


class RepositoryStatus(Enum):
    """Enumeration of possible operational states for a monitored repository."""

    IDLE = auto()
    CHANGED = auto()
    TRIGGERED = auto()
    PROCESSING = auto()
    STAGING = auto()
    REVIEWING = auto()
    TESTING = auto()
    ANALYZING = auto()
    GENERATING_COMMIT = auto()
    COMMITTING = auto()
    PUSHING = auto()
    ERROR = auto()


STATUS_EMOJI_MAP = {
    RepositoryStatus.IDLE: "▶️",
    RepositoryStatus.CHANGED: "📝",
    RepositoryStatus.TRIGGERED: "🎯",
    RepositoryStatus.PROCESSING: "🔄",
    RepositoryStatus.STAGING: "📦",
    RepositoryStatus.REVIEWING: "👀",
    RepositoryStatus.TESTING: "🔬",
    RepositoryStatus.ANALYZING: "🤔",
    RepositoryStatus.GENERATING_COMMIT: "✍️",
    RepositoryStatus.COMMITTING: "💾",
    RepositoryStatus.PUSHING: "🚀",
    RepositoryStatus.ERROR: "❌",
}


@mutable(slots=True)
class RepositoryState:
    """
    Holds the dynamic state for a single monitored repository.
    """
    repo_id: str = field()
    status: RepositoryStatus = field(default=RepositoryStatus.IDLE)
    last_change_time: datetime | None = field(default=None)
    last_commit_timestamp: datetime | None = field(default=None)
    save_count: int = field(default=0)
    error_message: str | None = field(default=None)
    inactivity_timer_handle: asyncio.TimerHandle | None = field(default=None)
    debounce_timer_handle: asyncio.TimerHandle | None = field(default=None)
    display_status_emoji: str = field(default="❓")
    active_rule_description: str | None = field(default=None)
    last_commit_short_hash: str | None = field(default=None)
    last_commit_message_summary: str | None = field(default=None)
    current_branch: str | None = field(default=None)
    rule_emoji: str | None = field(default=None)
    rule_dynamic_indicator: str | None = field(default=None)
    action_description: str | None = field(default=None)
    action_progress_total: int | None = field(default=None)
    action_progress_completed: int | None = field(default=None)
    is_paused: bool = field(default=False)
    pause_until: datetime | None = field(default=None)
    is_frozen: bool = field(default=False)
    freeze_reason: str | None = field(default=None)
    is_stopped: bool = field(default=False)
    is_refreshing: bool = field(default=False)
    timer_seconds_left: int | None = field(default=None)
    total_files: int = field(default=0)
    changed_files: int = field(default=0)
    added_files: int = field(default=0)
    deleted_files: int = field(default=0)
    modified_files: int = field(default=0)
    has_uncommitted_changes: bool = field(default=False)
    _timer_total_seconds: int | None = field(default=None, init=False)
    _timer_start_time: float | None = field(default=None, init=False)

    def __attrs_post_init__(self):
        """Log the initial state upon creation and set initial emoji."""
        self._update_display_emoji()
        log.debug(
            "Initialized repository state",
            repo_id=self.repo_id,
            initial_status=self.status.name,
            emoji=self.display_status_emoji,
        )

    def update_status(self, new_status: RepositoryStatus, error_msg: str | None = None) -> None:
        """Safely updates the status and optionally logs errors or recovery."""
        old_status = self.status
        if old_status == new_status:
            return

        self.status = new_status
        self._update_display_emoji()
        log_func = log.debug

        if new_status == RepositoryStatus.ERROR:
            self.error_message = error_msg or "Unknown error"
            log_func = log.warning
        elif old_status == RepositoryStatus.ERROR and new_status != RepositoryStatus.ERROR:
            log.info(
                "Repository status recovered from ERROR",
                repo_id=self.repo_id,
                new_status=new_status.name,
            )
            self.error_message = None

        log_func(
            "Repository status changed",
            repo_id=self.repo_id,
            old_status=old_status.name,
            new_status=new_status.name,
            **({"error": self.error_message} if new_status == RepositoryStatus.ERROR else {}),
        )

        if new_status in (RepositoryStatus.IDLE, RepositoryStatus.CHANGED):
            self.cancel_inactivity_timer()

    def record_change(self) -> None:
        """Records a file change event, updating time and count, and sets status to CHANGED."""
        now_utc = datetime.now(UTC)
        self.last_change_time = now_utc
        self.save_count += 1
        self.update_status(RepositoryStatus.CHANGED)
        log.info(
            "Recorded file change",
            repo_id=self.repo_id,
            change_time_utc=now_utc.isoformat(),
            new_save_count=self.save_count,
            current_status=self.status.name,
        )
        self.cancel_inactivity_timer()

    def reset_after_action(self) -> None:
        """Resets state fields typically after a successful commit/push sequence."""
        log.debug("Resetting state after action", repo_id=self.repo_id)
        self.save_count = 0
        self.active_rule_description = None
        self.rule_dynamic_indicator = None
        self.action_description = None
        self.action_progress_total = None
        self.action_progress_completed = None
        self.has_uncommitted_changes = False
        self.cancel_inactivity_timer()
        self.cancel_debounce_timer()
        self.update_status(RepositoryStatus.IDLE)

    def set_inactivity_timer(self, handle: asyncio.TimerHandle, total_seconds: int) -> None:
        """Stores the handle for a scheduled inactivity timer, cancelling any previous one."""
        self.cancel_inactivity_timer()
        self.inactivity_timer_handle = handle
        self._timer_total_seconds = total_seconds
        self._timer_start_time = asyncio.get_event_loop().time()
        log.debug("Inactivity timer set", repo_id=self.repo_id, timer_handle=repr(handle), total_seconds=total_seconds)

    def cancel_inactivity_timer(self) -> None:
        """Cancels the pending inactivity timer, if one exists."""
        if self.inactivity_timer_handle:
            self.inactivity_timer_handle.cancel()
            self.inactivity_timer_handle = None
            self._timer_total_seconds = None
            self._timer_start_time = None
            self.timer_seconds_left = None

    def set_debounce_timer(self, handle: asyncio.TimerHandle) -> None:
        """Stores the handle for a scheduled debounce timer, cancelling any previous one."""
        self.cancel_debounce_timer()
        self.debounce_timer_handle = handle

    def cancel_debounce_timer(self) -> None:
        """Cancels the pending debounce timer, if one exists."""
        if self.debounce_timer_handle:
            self.debounce_timer_handle.cancel()
            self.debounce_timer_handle = None

    def update_timer_countdown(self) -> None:
        """Updates the timer_seconds_left based on elapsed time."""
        if self.inactivity_timer_handle and self._timer_start_time and self._timer_total_seconds:
            elapsed = asyncio.get_event_loop().time() - self._timer_start_time
            seconds_left = max(0, int(self._timer_total_seconds - elapsed))
            self.timer_seconds_left = seconds_left
        else:
            self.timer_seconds_left = None

    def _update_display_emoji(self) -> None:
        """Internal method to update the display_status_emoji based on current state."""
        if self.is_stopped:
            self.display_status_emoji = "⏹️"
        elif self.is_paused:
            self.display_status_emoji = "⏸️"
        elif self.is_refreshing:
            self.display_status_emoji = "🔄"
        else:
            self.display_status_emoji = STATUS_EMOJI_MAP.get(self.status, "❓")

# 🔼⚙️

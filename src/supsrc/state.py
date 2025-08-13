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

    IDLE = auto()  # No changes detected or operation complete.
    CHANGED = auto()  # Changes detected, awaiting trigger condition.
    TRIGGERED = auto()  # Trigger condition met, action pending/queued.
    PROCESSING = auto()
    STAGING = auto()
    COMMITTING = auto()  # Git commit operation in progress.
    PUSHING = auto()  # Git push operation in progress.
    ERROR = auto()  # An error occurred, requires attention or clears on next success.


# Mapping of RepositoryStatus to display emojis for TUI
# Note: Some TUI statuses like 'Committed', 'Skipped', 'Evaluating', 'Waiting'
# might be derived in the TUI or Orchestrator based on a combination of
# RepositoryStatus and other state fields (e.g., error_message, last_commit_hash).
STATUS_EMOJI_MAP = {
    RepositoryStatus.IDLE: "▶️",      # Clean/normal state - ready to monitor
    RepositoryStatus.CHANGED: "📝",   # Uncommitted changes
    RepositoryStatus.TRIGGERED: "🎯", # Rule met, action pending
    RepositoryStatus.PROCESSING: "🔄", # General processing (e.g. status check)
    RepositoryStatus.STAGING: "📦",
    RepositoryStatus.COMMITTING: "💾",
    RepositoryStatus.PUSHING: "🚀",
    RepositoryStatus.ERROR: "❌",     # Error state
    # Specific states like 'Evaluating' or 'Waiting' will be set directly by Orchestrator
    # as they are not direct RepositoryStatus enum members.
}


@mutable(slots=True)
class RepositoryState:
    """
    Holds the dynamic state for a single monitored repository.

    This class is mutable because its fields are updated frequently during
    the monitoring process (e.g., last change time, status, timer handles).
    """

    repo_id: str = field()  # The unique identifier for the repository
    status: RepositoryStatus = field(default=RepositoryStatus.IDLE)
    last_change_time: datetime | None = field(default=None)  # Timezone-aware (UTC)
    last_commit_timestamp: datetime | None = field(default=None)  # Actual Git commit timestamp
    save_count: int = field(default=0)
    error_message: str | None = field(default=None)
    # Holds the handle for the asyncio timer used by inactivity triggers.
    # This allows cancellation if new changes arrive before the timer fires.
    inactivity_timer_handle: asyncio.TimerHandle | None = field(default=None)

    # New fields for TUI
    display_status_emoji: str = field(default="❓")  # Placeholder emoji
    active_rule_description: str | None = field(
        default=None
    )  # May become redundant with new fields
    last_commit_short_hash: str | None = field(default=None)
    last_commit_message_summary: str | None = field(default=None)
    current_branch: str | None = field(default=None)  # Current git branch

    # New fields for advanced TUI (rule emojis, dynamic indicators, progress bars)
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
    timer_seconds_left: int | None = field(default=None)  # Countdown for timer column

    # File statistics
    total_files: int = field(default=0)  # Total number of files in repo
    changed_files: int = field(default=0)  # Number of files with changes
    added_files: int = field(default=0)  # Number of added files
    deleted_files: int = field(default=0)  # Number of deleted files
    modified_files: int = field(default=0)  # Number of modified files
    has_uncommitted_changes: bool = field(default=False)  # Whether there are uncommitted changes

    # Internal timer tracking fields (not exposed via attrs)
    _timer_total_seconds: int | None = field(default=None, init=False)
    _timer_start_time: float | None = field(default=None, init=False)

    def __attrs_post_init__(self):
        """Log the initial state upon creation and set initial emoji."""
        self._update_display_emoji() # Call the new method to set initial emoji
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
            # No actual change, maybe log at debug if needed, but often noisy
            # log.debug("Status update requested but unchanged", repo_id=self.repo_id, status=new_status.name)
            return

        self.status = new_status
        # Update emoji based on the new status, with overrides for pause/stop/refresh.
        if self.is_stopped:
            self.display_status_emoji = "⏹️"
        elif self.is_paused:
            self.display_status_emoji = "⏸️"
        elif self.is_refreshing:
            self.display_status_emoji = "🔄"
        else:
            self.display_status_emoji = STATUS_EMOJI_MAP.get(new_status, "❓")
        log_func = log.debug  # Default log level for status changes

        if new_status == RepositoryStatus.ERROR:
            self.error_message = error_msg or "Unknown error"
            log_func = log.warning  # Elevate log level for errors
            # Optionally set last_error_time here
        elif old_status == RepositoryStatus.ERROR and new_status != RepositoryStatus.ERROR:
            log.info(  # Log recovery specifically at INFO level
                "Repository status recovered from ERROR",
                repo_id=self.repo_id,
                new_status=new_status.name,
            )
            self.error_message = None  # Clear previous error on recovery
            # Fall through to log the specific transition below if desired,
            # or return here if the recovery message is sufficient.

        # Log the specific transition details
        log_func(
            "Repository status changed",
            repo_id=self.repo_id,
            old_status=old_status.name,
            new_status=new_status.name,
            **({"error": self.error_message} if new_status == RepositoryStatus.ERROR else {}),
        )

        # Reset relevant fields on transition back to IDLE or CHANGED?
        if new_status in (RepositoryStatus.IDLE, RepositoryStatus.CHANGED):
            self.cancel_inactivity_timer()  # Ensure timer is cleared if we reset state
            # Save count is typically reset only after successful commit/push in reset_after_action

    def record_change(self) -> None:
        """Records a file change event, updating time and count, and sets status to CHANGED."""
        now_utc = datetime.now(UTC)
        self.last_change_time = now_utc
        self.save_count += 1
        self.update_status(RepositoryStatus.CHANGED)  # Move to CHANGED state
        log.info(
            "Recorded file change",
            repo_id=self.repo_id,
            change_time_utc=now_utc.isoformat(),
            new_save_count=self.save_count,
            current_status=self.status.name,
        )
        # Cancel any pending inactivity timer, as a new change just arrived.
        self.cancel_inactivity_timer()

    def reset_after_action(self) -> None:
        """Resets state fields typically after a successful commit/push sequence."""
        log.debug("Resetting state after action", repo_id=self.repo_id)
        self.save_count = 0
        self.active_rule_description = None  # Clear specific action/wait messages
        # self.error_message is cleared by update_status if moving out of ERROR

        # Reset new TUI fields
        self.rule_emoji = None  # Or reset to default based on config for next cycle
        self.rule_dynamic_indicator = None  # Or reset to default
        self.action_description = None
        self.action_progress_total = None
        self.action_progress_completed = None
        self.has_uncommitted_changes = False

        self.cancel_inactivity_timer()  # Ensure timer is gone
        self.update_status(RepositoryStatus.IDLE)  # Back to idle state
        # Note: last_commit_short_hash and last_commit_message_summary are intentionally persisted

    def set_inactivity_timer(self, handle: asyncio.TimerHandle, total_seconds: int) -> None:
        """Stores the handle for a scheduled inactivity timer, cancelling any previous one."""
        # Cancel any previous timer before setting a new one
        self.cancel_inactivity_timer()
        self.inactivity_timer_handle = handle
        self._timer_total_seconds = total_seconds
        self._timer_start_time = asyncio.get_event_loop().time()
        log.debug("Inactivity timer set", repo_id=self.repo_id, timer_handle=repr(handle), total_seconds=total_seconds)

    def cancel_inactivity_timer(self) -> None:
        """Cancels the pending inactivity timer, if one exists."""
        if self.inactivity_timer_handle:
            timer_repr = repr(self.inactivity_timer_handle)  # Capture before cancelling
            log.debug(
                "Cancelling existing inactivity timer",
                repo_id=self.repo_id,
                timer_handle=timer_repr,
            )
            try:
                self.inactivity_timer_handle.cancel()
            except Exception as e:
                # Log potential errors during cancellation, though usually straightforward
                log.warning(
                    "Error cancelling timer handle",
                    repo_id=self.repo_id,
                    timer_handle=timer_repr,
                    error=str(e),
                )
            finally:
                self.inactivity_timer_handle = None
                self._timer_total_seconds = None
                self._timer_start_time = None
                self.timer_seconds_left = None
        else:
            # This is normal operation, no need to log unless debugging timing issues
            # log.debug("No active inactivity timer to cancel", repo_id=self.repo_id)
            pass

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
        old_emoji = self.display_status_emoji
        if self.is_stopped:
            self.display_status_emoji = "⏹️"
        elif self.is_paused:
            self.display_status_emoji = "⏸️"
        elif self.is_refreshing:
            self.display_status_emoji = "🔄"
        else:
            # Fallback to status-based emoji if not stopped, paused, or refreshing
            self.display_status_emoji = STATUS_EMOJI_MAP.get(self.status, "❓")

        if old_emoji != self.display_status_emoji:
            log.debug(
                f"Updated emoji for {self.repo_id}: '{old_emoji}' -> '{self.display_status_emoji}' "
                f"(paused={self.is_paused}, stopped={self.is_stopped}, status={self.status.name})"
            )

# 🔼⚙️

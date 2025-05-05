#
# supsrc/state.py
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
    TRIGGERED = auto() # Trigger condition met, action pending/queued.
    PROCESSING = auto()
    STAGING = auto()
    COMMITTING = auto()  # Git commit operation in progress.
    PUSHING = auto()  # Git push operation in progress.
    ERROR = auto()  # An error occurred, requires attention or clears on next success.


@mutable(slots=True)
class RepositoryState:
    """
    Holds the dynamic state for a single monitored repository.

    This class is mutable because its fields are updated frequently during
    the monitoring process (e.g., last change time, status, timer handles).
    """

    repo_id: str = field()  # The unique identifier for the repository
    status: RepositoryStatus = field(default=RepositoryStatus.IDLE)
    last_change_time: datetime | None = field(default=None) # Timezone-aware (UTC)
    save_count: int = field(default=0)
    error_message: str | None = field(default=None)
    # Holds the handle for the asyncio timer used by inactivity triggers.
    # This allows cancellation if new changes arrive before the timer fires.
    inactivity_timer_handle: asyncio.TimerHandle | None = field(default=None)

    # Consider adding:
    # last_commit_hash: Optional[str] = field(default=None)
    # last_push_time: Optional[datetime] = field(default=None)
    # last_error_time: Optional[datetime] = field(default=None)

    def __attrs_post_init__(self):
        """Log the initial state upon creation."""
        log.debug(
            "Initialized repository state",
            repo_id=self.repo_id,
            initial_status=self.status.name,
        )

    def update_status(self, new_status: RepositoryStatus, error_msg: str | None = None) -> None:
        """ Safely updates the status and optionally logs errors or recovery. """
        old_status = self.status
        if old_status == new_status:
            # No actual change, maybe log at debug if needed, but often noisy
            # log.debug("Status update requested but unchanged", repo_id=self.repo_id, status=new_status.name)
            return

        self.status = new_status
        log_func = log.debug # Default log level for status changes

        if new_status == RepositoryStatus.ERROR:
            self.error_message = error_msg or "Unknown error"
            log_func = log.warning # Elevate log level for errors
            # Optionally set last_error_time here
        elif old_status == RepositoryStatus.ERROR and new_status != RepositoryStatus.ERROR:
             log.info( # Log recovery specifically at INFO level
                 "Repository status recovered from ERROR",
                 repo_id=self.repo_id,
                 new_status=new_status.name,
             )
             self.error_message = None # Clear previous error on recovery
             # Fall through to log the specific transition below if desired,
             # or return here if the recovery message is sufficient.

        # Log the specific transition details
        log_func(
            "Repository status changed",
            repo_id=self.repo_id,
            old_status=old_status.name,
            new_status=new_status.name,
            **({"error": self.error_message} if new_status == RepositoryStatus.ERROR else {})
        )

        # Reset relevant fields on transition back to IDLE or CHANGED?
        if new_status in (RepositoryStatus.IDLE, RepositoryStatus.CHANGED):
             self.cancel_inactivity_timer() # Ensure timer is cleared if we reset state
             # Save count is typically reset only after successful commit/push in reset_after_action

    def record_change(self) -> None:
        """Records a file change event, updating time and count, and sets status to CHANGED."""
        now_utc = datetime.now(UTC)
        self.last_change_time = now_utc
        self.save_count += 1
        self.update_status(RepositoryStatus.CHANGED) # Move to CHANGED state
        log.debug(
            "Recorded file change",
            repo_id=self.repo_id,
            change_time_utc=now_utc.isoformat(),
            new_save_count=self.save_count,
            current_status=self.status.name,
        )
        # Cancel any pending inactivity timer, as a new change just arrived.
        self.cancel_inactivity_timer()

    def reset_after_action(self) -> None:
        """ Resets state fields typically after a successful commit/push sequence. """
        log.debug("Resetting state after action", repo_id=self.repo_id)
        self.save_count = 0
        # Keep last_change_time as the time of the action, or clear it?
        # Clearing might be simpler for inactivity logic.
        self.last_change_time = None
        self.cancel_inactivity_timer() # Ensure timer is gone
        self.update_status(RepositoryStatus.IDLE) # Back to idle state


    def set_inactivity_timer(self, handle: asyncio.TimerHandle) -> None:
        """Stores the handle for a scheduled inactivity timer, cancelling any previous one."""
        # Cancel any previous timer before setting a new one
        self.cancel_inactivity_timer()
        self.inactivity_timer_handle = handle
        log.debug("Inactivity timer set", repo_id=self.repo_id, timer_handle=repr(handle))

    def cancel_inactivity_timer(self) -> None:
        """Cancels the pending inactivity timer, if one exists."""
        if self.inactivity_timer_handle:
            timer_repr = repr(self.inactivity_timer_handle) # Capture before cancelling
            log.debug("Cancelling existing inactivity timer", repo_id=self.repo_id, timer_handle=timer_repr)
            try:
                 self.inactivity_timer_handle.cancel()
            except Exception as e:
                 # Log potential errors during cancellation, though usually straightforward
                 log.warning("Error cancelling timer handle", repo_id=self.repo_id, timer_handle=timer_repr, error=str(e))
            finally:
                 self.inactivity_timer_handle = None
        else:
             # This is normal operation, no need to log unless debugging timing issues
             # log.debug("No active inactivity timer to cancel", repo_id=self.repo_id)
             pass

# 🔼⚙️

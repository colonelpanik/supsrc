# src/supsrc/runtime/event_processor.py
"""
Consumes filesystem events, checks rules, manages timers, and triggers actions.
"""
import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Set

import structlog

from supsrc.config import InactivityRuleConfig, RepositoryConfig, SupsrcConfig
from supsrc.monitor import MonitoredEvent
from supsrc.rules import check_trigger_condition
from supsrc.runtime.action_handler import ActionHandler
from supsrc.runtime.tui_interface import TUIInterface
from supsrc.state import RepositoryState, RepositoryStatus
from supsrc.telemetry import StructLogger

if TYPE_CHECKING:
    from supsrc.runtime.orchestrator import WatchOrchestrator

log: StructLogger = structlog.get_logger("runtime.event_processor")
# Debounce delay to group rapid file system events (e.g., create + modify) into one action.
DEBOUNCE_DELAY = 0.25  # 250 milliseconds


class EventProcessor:
    """Consumes events, checks rules, and delegates actions."""

    def __init__(
        self,
        orchestrator: "WatchOrchestrator",
        config: SupsrcConfig,
        event_queue: asyncio.Queue[MonitoredEvent],
        shutdown_event: asyncio.Event,
        action_handler: ActionHandler,
        repo_states: dict[str, RepositoryState],
        tui: TUIInterface,
    ):
        self.orchestrator = orchestrator
        self.config = config
        self.event_queue = event_queue
        self.shutdown_event = shutdown_event
        self.action_handler = action_handler
        self.repo_states = repo_states
        self.tui = tui
        self._action_tasks: set[asyncio.Task] = set()
        self._recent_moves: Set[Path] = set()
        log.debug("EventProcessor initialized.")

    async def run(self) -> None:
        """Main event consumption loop."""
        log.info("Event processor is running.")
        loop = asyncio.get_running_loop()

        while not self.shutdown_event.is_set():
            try:
                # Gracefully wait for either an event or a shutdown signal
                get_task = asyncio.create_task(self.event_queue.get())
                shutdown_task = asyncio.create_task(self.shutdown_event.wait())
                done, pending = await asyncio.wait(
                    {get_task, shutdown_task}, return_when=asyncio.FIRST_COMPLETED
                )

                if shutdown_task in done:
                    get_task.cancel()
                    break

                event = get_task.result()
                shutdown_task.cancel()

                # Handle special config reload event
                if event.repo_id == "__config__":
                    log.info("Configuration change event received, triggering reload.")
                    asyncio.create_task(self.orchestrator.reload_config())
                    continue

                # Get state and check if processing is paused
                repo_state = self.repo_states.get(event.repo_id)
                if not repo_state:
                    log.warning("Ignoring event for unknown repo", repo_id=event.repo_id)
                    continue
                if repo_state.is_paused or self.orchestrator._is_paused:
                    log.debug("Repo or orchestrator is paused, event ignored", repo_id=event.repo_id)
                    continue

                # Deduplicate moved/deleted events
                if event.event_type == "moved":
                    self._recent_moves.add(event.src_path)
                    loop.call_later(0.5, self._recent_moves.discard, event.src_path)
                elif event.event_type == "deleted" and event.src_path in self._recent_moves:
                    log.debug("Ignoring duplicate delete event for a moved file", path=str(event.src_path))
                    continue

                # Record the change and update UI
                repo_state.record_change()
                self.tui.post_state_update(self.repo_states)
                
                # Instead of acting immediately, start a debounced check
                self._debounce_trigger_check(event.repo_id)

            except asyncio.CancelledError:
                log.info("Event processor run loop cancelled.")
                break
            except Exception:
                log.exception("Error in event processor loop.")
        
        await self.stop()
        log.info("Event processor has stopped.")

    def _debounce_trigger_check(self, repo_id: str):
        """Schedules a trigger check to run after a short delay, canceling any pending one."""
        repo_state = self.repo_states.get(repo_id)
        if not repo_state:
            return

        repo_state.cancel_debounce_timer()
        loop = asyncio.get_running_loop()
        handle = loop.call_later(DEBOUNCE_DELAY, self._execute_trigger_check, repo_id)
        repo_state.set_debounce_timer(handle)
        log.debug("Debounce timer set", repo_id=repo_id, delay=DEBOUNCE_DELAY)

    def _execute_trigger_check(self, repo_id: str):
        """Called by the debounce timer. Checks rules and triggers the appropriate action."""
        repo_state = self.repo_states.get(repo_id)
        repo_config = self.config.repositories.get(repo_id)
        if not repo_state or not repo_config:
            return

        log.debug("Executing debounced trigger check", repo_id=repo_id)

        # Do not proceed if an action is already in progress for this repo
        if repo_state.status not in (RepositoryStatus.IDLE, RepositoryStatus.CHANGED):
            log.debug("Action already in progress, skipping trigger check", repo_id=repo_id, status=repo_state.status.name)
            return

        if check_trigger_condition(repo_state, repo_config):
            self._schedule_action(repo_id)
        elif isinstance(repo_config.rule, InactivityRuleConfig):
            # If the save count rule wasn't met, the inactivity rule might still apply
            self._start_inactivity_timer(repo_state, repo_config)

    def _schedule_action(self, repo_id: str) -> None:
        """Schedules the action handler to execute for a repo."""
        repo_state = self.repo_states.get(repo_id)
        if not repo_state:
            return

        # Set status to TRIGGERED to act as a lock
        repo_state.update_status(RepositoryStatus.TRIGGERED)
        # Clean up all timers for this repo before starting the action
        repo_state.cancel_inactivity_timer()
        repo_state.cancel_debounce_timer()
        
        log.info("Trigger condition met, scheduling action sequence.", repo_id=repo_id)
        task = asyncio.create_task(self.action_handler.execute_action_sequence(repo_id))
        self._action_tasks.add(task)
        task.add_done_callback(self._action_tasks.discard)

    def _start_inactivity_timer(self, state: RepositoryState, config: RepositoryConfig) -> None:
        """Sets or resets an inactivity timer for a repository."""
        if not isinstance(config.rule, InactivityRuleConfig):
            return
        
        delay = config.rule.period.total_seconds()
        log.debug("Starting inactivity timer", repo_id=state.repo_id, delay=delay)
        
        loop = asyncio.get_running_loop()
        handle = loop.call_later(delay, self._schedule_action, state.repo_id)
        state.set_inactivity_timer(handle, int(delay))

    async def stop(self) -> None:
        """Gracefully stop all scheduled action tasks."""
        if not self._action_tasks:
            return
        log.debug("Stopping in-flight action tasks", count=len(self._action_tasks))
        for task in list(self._action_tasks):
            task.cancel()
        await asyncio.gather(*self._action_tasks, return_exceptions=True)
        log.debug("All action tasks cancelled.")

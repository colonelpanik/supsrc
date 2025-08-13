# src/supsrc/runtime/event_processor.py

"""
Consumes filesystem events, checks rules, manages timers, and triggers actions.
"""

import asyncio
from typing import TYPE_CHECKING, Set
from pathlib import Path

import structlog

from supsrc.config import InactivityRuleConfig, RepositoryConfig, SupsrcConfig
from supsrc.monitor import MonitoredEvent
from supsrc.rules import check_trigger_condition
from supsrc.runtime.action_handler import ActionHandler
from supsrc.runtime.tui_interface import TUIInterface
from supsrc.state import RepositoryState
from supsrc.telemetry import StructLogger

if TYPE_CHECKING:
    from supsrc.runtime.orchestrator import WatchOrchestrator


log: StructLogger = structlog.get_logger("runtime.event_processor")


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
        # Cache to track source paths of 'moved' events to de-duplicate delete events.
        self._recent_moves: Set[Path] = set()
        log.debug("EventProcessor initialized.")

    async def run(self) -> None:
        """Main event consumption loop."""
        log.info("Event processor is running.")
        loop = asyncio.get_running_loop()

        while not self.shutdown_event.is_set():
            try:
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
                if event is None:
                    continue

                # Handle special config change event
                if event.repo_id == "__config__":
                    log.info("Configuration change event received, triggering reload.")
                    asyncio.create_task(self.orchestrator.reload_config())
                    continue

                repo_id = event.repo_id
                repo_state = self.repo_states.get(repo_id)
                repo_config = self.config.repositories.get(repo_id)

                if not repo_state or not repo_config:
                    log.warning("Ignoring event for unknown/disabled repo", repo_id=repo_id)
                    continue

                if repo_state.is_paused or self.orchestrator._is_paused:
                    log.debug("Repo or orchestrator is paused, deferring event", repo_id=repo_id)
                    await self.event_queue.put(event)
                    await asyncio.sleep(1)
                    continue

                # --- Move/Rename De-duplication Logic ---
                if event.event_type == "moved":
                    # When a file is moved, cache its original path briefly.
                    src_path = event.src_path
                    self._recent_moves.add(src_path)
                    # Schedule removal from cache after a short delay.
                    loop.call_later(0.5, self._recent_moves.discard, src_path)
                    log.debug("Cached move source to prevent duplicate delete event", path=str(src_path))
                elif event.event_type == "deleted" and event.src_path in self._recent_moves:
                    # If a delete event arrives for a path we just moved, ignore it.
                    log.debug("Ignoring duplicate delete event for a moved file", path=str(event.src_path))
                    continue
                # --- End De-duplication Logic ---

                repo_state.record_change()
                self.tui.post_log_update(repo_id, "DEBUG", f"Change detected: {event.src_path.name}")
                self.tui.post_state_update(self.repo_states)

                if check_trigger_condition(repo_state, repo_config):
                    self._schedule_action(repo_id)
                elif isinstance(repo_config.rule, InactivityRuleConfig):
                    self._start_inactivity_timer(repo_state, repo_config)

            except asyncio.CancelledError:
                log.info("Event processor run loop cancelled.")
                break
            except Exception:
                log.exception("Error in event processor loop.")
        
        await self.stop()
        log.info("Event processor has stopped.")

    def _schedule_action(self, repo_id: str) -> None:
        """Schedules the action handler to execute for a repo."""
        repo_state = self.repo_states.get(repo_id)
        if not repo_state:
            return

        repo_state.cancel_inactivity_timer()
        
        log.debug("Scheduling action sequence", repo_id=repo_id)
        task = asyncio.create_task(self.action_handler.execute_action_sequence(repo_id))
        self._action_tasks.add(task)
        task.add_done_callback(self._action_tasks.discard)

    def _start_inactivity_timer(self, state: RepositoryState, config: RepositoryConfig) -> None:
        """Sets or resets an inactivity timer for a repository."""
        if not isinstance(config.rule, InactivityRuleConfig):
            return
            
        delay = config.rule.period.total_seconds()
        log.debug("Scheduling inactivity check", repo_id=state.repo_id, delay=delay)
        
        loop = asyncio.get_running_loop()
        timer_handle = loop.call_later(
            delay,
            self._schedule_action,
            state.repo_id
        )
        state.set_inactivity_timer(timer_handle, int(delay))

    async def stop(self) -> None:
        """Gracefully stop all scheduled action tasks."""
        if not self._action_tasks:
            return
        log.debug("Stopping in-flight action tasks", count=len(self._action_tasks))
        for task in list(self._action_tasks):
            task.cancel()
        await asyncio.gather(*self._action_tasks, return_exceptions=True)
        log.debug("All action tasks cancelled.")

"""Dedicated thread that runs the cron scheduler on a long-lived asyncio loop.

In the GUI, the scheduler must run on a persistent loop. MessageWorker and
SchedulerDialog use short-lived loops that close after each operation, which
would cancel the scheduler task. This module starts a single daemon thread
with its own event loop so scheduled tasks actually run every 30 minutes (or
whatever cron specifies).
"""

import asyncio
import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_scheduler_thread: Optional[threading.Thread] = None
_scheduler_started = threading.Lock()


def start_scheduler_thread(get_agent: Callable[[], Optional[object]]) -> None:
    """Start the dedicated scheduler thread if not already running.

    The thread runs a long-lived asyncio loop and calls the current agent's
    _ensure_scheduler_running(). Every 60 seconds it re-checks get_agent() so
    that when the user switches workspace, the new agent's scheduler is
    started and the old one stopped.

    Args:
        get_agent: Callable that returns the current AgentCore (or None).
    """
    global _scheduler_thread
    with _scheduler_started:
        if _scheduler_thread is not None and _scheduler_thread.is_alive():
            return

        def run_loop() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_scheduler_loop(get_agent))
            except Exception as e:
                logger.exception("Scheduler thread exited: %s", e)
            finally:
                loop.close()

        _scheduler_thread = threading.Thread(
            target=run_loop,
            name="GrizzyClaw-Scheduler",
            daemon=True,
        )
        _scheduler_thread.start()
        logger.info("Scheduler thread started (dedicated loop for GUI)")


async def _scheduler_loop(get_agent: Callable[[], Optional[object]]) -> None:
    """Run the scheduler for the current agent; re-check get_agent every 60s."""
    # Wait for agent to be available (main window may still be initializing)
    for _ in range(15):
        agent = get_agent()
        if agent is not None:
            break
        await asyncio.sleep(1)
    else:
        logger.warning("Scheduler thread: no agent after 15s, will keep retrying")

    current_agent = None
    while True:
        agent = get_agent()
        if agent is not current_agent:
            if current_agent is not None and getattr(
                getattr(current_agent, "scheduler", None), "running", False
            ):
                try:
                    await current_agent.scheduler.stop()
                    logger.debug("Stopped scheduler for previous agent")
                except Exception as e:
                    logger.warning("Error stopping previous scheduler: %s", e)
            current_agent = agent

        if agent is not None and getattr(agent, "_ensure_scheduler_running", None):
            try:
                await agent._ensure_scheduler_running()
                # Let the scheduler's _run_loop task run at least one tick
                await asyncio.sleep(1)
            except Exception as e:
                logger.warning("Scheduler ensure failed: %s", e)

        await asyncio.sleep(59)

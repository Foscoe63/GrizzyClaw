"""Cron-based task scheduler for automation"""

import logging
import asyncio
from typing import Callable, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from croniter import croniter

logger = logging.getLogger(__name__)


@dataclass
class ScheduledTask:
    """A scheduled task"""
    id: str
    name: str
    cron_expression: str
    handler: Callable
    enabled: bool = True
    metadata: Dict = field(default_factory=dict)
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    run_count: int = 0

    def __post_init__(self):
        """Calculate next run time"""
        if self.enabled:
            self._calculate_next_run()

    def _calculate_next_run(self):
        """Calculate next run time based on cron expression"""
        try:
            now = datetime.now()
            cron = croniter(self.cron_expression, now)
            self.next_run = cron.get_next(datetime)
        except Exception as e:
            logger.error(f"Invalid cron expression '{self.cron_expression}': {e}")
            self.enabled = False


class CronScheduler:
    """Cron-based task scheduler

    Supports standard cron expressions:
    - * * * * * (every minute)
    - 0 * * * * (every hour)
    - 0 0 * * * (daily at midnight)
    - 0 0 * * 0 (weekly on Sunday)
    - 0 0 1 * * (monthly on 1st)
    """

    def __init__(self):
        self.tasks: Dict[str, ScheduledTask] = {}
        self.running = False
        self._task = None

    def schedule(
        self,
        task_id: str,
        name: str,
        cron_expression: str,
        handler: Callable,
        metadata: Optional[Dict] = None
    ) -> ScheduledTask:
        """Schedule a task

        Args:
            task_id: Unique task identifier
            name: Human-readable name
            cron_expression: Cron expression (e.g., "0 * * * *")
            handler: Async function to execute
            metadata: Additional metadata

        Returns:
            ScheduledTask instance
        """
        task = ScheduledTask(
            id=task_id,
            name=name,
            cron_expression=cron_expression,
            handler=handler,
            metadata=metadata or {}
        )

        self.tasks[task_id] = task
        logger.info(f"Scheduled task '{name}' ({task_id}): {cron_expression}")
        logger.info(f"  Next run: {task.next_run}")

        return task

    def unschedule(self, task_id: str) -> bool:
        """Unschedule a task

        Args:
            task_id: Task identifier

        Returns:
            True if removed, False if not found
        """
        if task_id in self.tasks:
            del self.tasks[task_id]
            logger.info(f"Unscheduled task: {task_id}")
            return True
        return False

    def enable_task(self, task_id: str):
        """Enable a task"""
        if task_id in self.tasks:
            self.tasks[task_id].enabled = True
            self.tasks[task_id]._calculate_next_run()
            logger.info(f"Enabled task: {task_id}")

    def disable_task(self, task_id: str):
        """Disable a task"""
        if task_id in self.tasks:
            self.tasks[task_id].enabled = False
            logger.info(f"Disabled task: {task_id}")

    async def start(self):
        """Start the scheduler"""
        logger.info("Starting cron scheduler...")
        self.running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"✓ Cron scheduler started with {len(self.tasks)} tasks")

    async def stop(self):
        """Stop the scheduler"""
        logger.info("Stopping cron scheduler...")
        self.running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        logger.info("✓ Cron scheduler stopped")

    async def _run_loop(self):
        """Main scheduler loop"""
        logger.info("Entering scheduler loop...")

        try:
            while self.running:
                now = datetime.now()

                # Check each task
                for task_id, task in list(self.tasks.items()):
                    if not task.enabled:
                        continue

                    if task.next_run and now >= task.next_run:
                        # Time to run this task
                        logger.info(f"Executing scheduled task: {task.name}")

                        try:
                            # Execute handler
                            if asyncio.iscoroutinefunction(task.handler):
                                await task.handler()
                            else:
                                task.handler()

                            task.run_count += 1
                            task.last_run = now
                            logger.info(f"✓ Task '{task.name}' completed")

                        except Exception as e:
                            logger.error(f"✗ Task '{task.name}' failed: {e}", exc_info=True)

                        # Calculate next run
                        task._calculate_next_run()
                        logger.info(f"  Next run: {task.next_run}")

                # Sleep for a bit before checking again
                await asyncio.sleep(30)  # Check every 30 seconds

        except Exception as e:
            logger.error(f"Scheduler loop error: {e}", exc_info=True)
            self.running = False

    def get_stats(self) -> Dict:
        """Get scheduler statistics

        Returns:
            Statistics dictionary
        """
        total = len(self.tasks)
        enabled = sum(1 for t in self.tasks.values() if t.enabled)
        disabled = total - enabled

        return {
            "total_tasks": total,
            "enabled": enabled,
            "disabled": disabled,
            "running": self.running,
            "tasks": [
                {
                    "id": task.id,
                    "name": task.name,
                    "cron": task.cron_expression,
                    "enabled": task.enabled,
                    "last_run": task.last_run.isoformat() if task.last_run else None,
                    "next_run": task.next_run.isoformat() if task.next_run else None,
                    "run_count": task.run_count
                }
                for task in self.tasks.values()
            ]
        }

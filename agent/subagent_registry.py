"""Registry for sub-agent runs: track active and completed spawns with policy and lifecycle."""

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class SubagentStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


@dataclass
class SubagentRun:
    """A single sub-agent run (spawned by parent agent or user)."""
    run_id: str
    task: str
    label: str
    status: SubagentStatus = SubagentStatus.RUNNING
    workspace_id: str = ""
    parent_run_id: Optional[str] = None
    spawn_depth: int = 1
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    result: str = ""
    error: str = ""
    # Optional model/timeout override used for this run
    model_override: Optional[str] = None
    run_timeout_seconds: Optional[int] = None
    # For UI: task summary (first line of task)
    task_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task": self.task[:200] + "…" if len(self.task) > 200 else self.task,
            "task_summary": self.task_summary,
            "label": self.label,
            "status": self.status.value,
            "workspace_id": self.workspace_id,
            "parent_run_id": self.parent_run_id,
            "spawn_depth": self.spawn_depth,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "result": self.result[:2000] + "…" if len(self.result) > 2000 else self.result,
            "error": self.error,
            "model_override": self.model_override,
            "run_timeout_seconds": self.run_timeout_seconds,
        }


class SubagentRegistry:
    """
    In-memory registry of sub-agent runs. Tracks active and recent completed runs
    for policy (max depth, max children) and GUI (list, kill, show results).
    """
    def __init__(self, max_recent_completed: int = 100):
        self._runs: Dict[str, SubagentRun] = {}
        self._max_recent_completed = max_recent_completed
        self._completed_order: List[str] = []  # run_id order for "recent"
        # List (not set) so we never require hashable run_id; avoids "cannot use 'dict' as a set element"
        self._cancel_requested: List[str] = []

    def register(
        self,
        task: str,
        workspace_id: str,
        parent_run_id: Optional[str] = None,
        spawn_depth: int = 1,
        label: str = "",
        model_override: Optional[str] = None,
        run_timeout_seconds: Optional[int] = None,
    ) -> SubagentRun:
        # Ensure parent_run_id and workspace_id are str or None (never dict/unhashable);
        # avoids "cannot use 'dict' as a set element" if any caller passes context by mistake.
        pid = parent_run_id if isinstance(parent_run_id, (str, type(None))) else None
        if isinstance(workspace_id, dict):
            wid = ""
        elif isinstance(workspace_id, str):
            wid = workspace_id
        else:
            wid = str(workspace_id or "")
        run_id = f"sub_{uuid.uuid4().hex[:12]}"
        task_summary = (task.strip().split("\n")[0][:80] + "…") if len(task.strip().split("\n")[0]) > 80 else (task.strip().split("\n")[0] or "")
        run = SubagentRun(
            run_id=run_id,
            task=task,
            label=label or task_summary or "Sub-agent",
            workspace_id=wid,
            parent_run_id=pid,
            spawn_depth=spawn_depth,
            task_summary=task_summary or "",
            model_override=model_override,
            run_timeout_seconds=run_timeout_seconds,
        )
        self._runs[run_id] = run
        logger.debug(
            "SubagentRegistry.register run_id=%s workspace_id=%s registry_id=%s total_runs=%d",
            run_id, wid, id(self), len(self._runs),
        )
        return run

    def get_debug_info(self) -> dict:
        """Return counts and sample ids for debug (no PII)."""
        running = [r.run_id for r in self._runs.values() if r.status == SubagentStatus.RUNNING]
        completed_count = len(self._completed_order)
        return {
            "registry_id": id(self),
            "total_runs": len(self._runs),
            "completed_order_len": completed_count,
            "running_count": len(running),
            "running_ids": running[:5],
            "completed_order_tail": self._completed_order[-10:] if self._completed_order else [],
        }

    def get(self, run_id: str) -> Optional[SubagentRun]:
        return self._runs.get(run_id)

    def complete(self, run_id: str, result: str = "") -> None:
        run = self._runs.get(run_id)
        if run:
            run.status = SubagentStatus.COMPLETED
            run.result = result or ""
            run.completed_at = time.time()
            self._completed_order.append(run_id)
            logger.debug(
                "SubagentRegistry.complete run_id=%s registry_id=%s completed_order_len=%d",
                run_id, id(self), len(self._completed_order),
            )
            if len(self._completed_order) > self._max_recent_completed:
                old_id = self._completed_order.pop(0)
                if self._runs.get(old_id) and self._runs[old_id].status != SubagentStatus.RUNNING:
                    del self._runs[old_id]

    def fail(self, run_id: str, error: str = "") -> None:
        run = self._runs.get(run_id)
        if run:
            run.status = SubagentStatus.FAILED
            run.error = error or "Unknown error"
            run.completed_at = time.time()
            self._completed_order.append(run_id)
            if len(self._completed_order) > self._max_recent_completed:
                old_id = self._completed_order.pop(0)
                if self._runs.get(old_id) and self._runs[old_id].status != SubagentStatus.RUNNING:
                    del self._runs[old_id]

    def timeout(self, run_id: str) -> None:
        run = self._runs.get(run_id)
        if run:
            run.status = SubagentStatus.TIMED_OUT
            run.error = "Run timed out"
            run.completed_at = time.time()
            self._completed_order.append(run_id)

    def cancel(self, run_id: str) -> None:
        rid = run_id if isinstance(run_id, str) else getattr(run_id, "run_id", None)
        if rid is not None and rid not in self._cancel_requested:
            self._cancel_requested.append(rid)
        run = self._runs.get(rid) if rid is not None else None
        if run and run.status == SubagentStatus.RUNNING:
            run.status = SubagentStatus.CANCELLED
            run.completed_at = time.time()
            if rid is not None:
                self._completed_order.append(rid)

    def is_cancel_requested(self, run_id: str) -> bool:
        rid = run_id if isinstance(run_id, str) else getattr(run_id, "run_id", None)
        return rid is not None and rid in self._cancel_requested

    def clear_cancel_flag(self, run_id: str) -> None:
        rid = run_id if isinstance(run_id, str) else getattr(run_id, "run_id", None)
        if rid is not None and rid in self._cancel_requested:
            self._cancel_requested.remove(rid)

    def count_active_children(self, parent_run_id: Optional[str], workspace_id: str) -> int:
        """Count runs that are still RUNNING and have the given parent (or no parent for main)."""
        count = 0
        for r in self._runs.values():
            if r.status != SubagentStatus.RUNNING:
                continue
            if parent_run_id is None:
                if r.parent_run_id is None and r.workspace_id == workspace_id:
                    count += 1
            else:
                if r.parent_run_id == parent_run_id:
                    count += 1
        return count

    def list_active(self, workspace_id: Optional[str] = None) -> List[SubagentRun]:
        out = [r for r in self._runs.values() if r.status == SubagentStatus.RUNNING]
        if workspace_id:
            out = [r for r in out if r.workspace_id == workspace_id]
        return sorted(out, key=lambda x: x.created_at)

    def list_recent_completed(self, limit: int = 50, workspace_id: Optional[str] = None) -> List[SubagentRun]:
        order = list(reversed(self._completed_order))[:limit]
        out: List[SubagentRun] = []
        for run_id in order:
            r = self._runs.get(run_id)
            if r and r.status != SubagentStatus.RUNNING:
                if workspace_id is None or r.workspace_id == workspace_id:
                    out.append(r)
        return out

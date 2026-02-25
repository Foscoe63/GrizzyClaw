"""Swarm activity feed: recent events from the swarm event bus."""

import time
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QHBoxLayout,
)
from PyQt6.QtCore import Qt

if TYPE_CHECKING:
    from grizzyclaw.workspaces import WorkspaceManager


def _format_event_data(data: dict) -> str:
    """Short one-line summary of event data for display."""
    parts = []
    for k, v in list(data.items())[:4]:
        if k in ("message", "task_summary", "summary"):
            v = (str(v)[:60] + "…") if len(str(v)) > 60 else str(v)
        parts.append(f"{k}={v}")
    return " | ".join(parts) if parts else ""


class SwarmActivityDialog(QDialog):
    """Shows last N swarm events (task_completed, consensus_ready, subtask_claimed, etc.)."""

    def __init__(self, workspace_manager: "WorkspaceManager", parent=None):
        super().__init__(parent)
        self.workspace_manager = workspace_manager
        self.setWindowTitle("Swarm activity")
        self.setMinimumSize(560, 400)
        self.setup_ui()
        self.refresh()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        self.hint = QLabel("Recent swarm events (delegations, claims, consensus).")
        self.hint.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.hint)
        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        layout.addWidget(self.list_widget)
        btn_layout = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        btn_layout.addWidget(refresh_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def refresh(self):
        self.list_widget.clear()
        bus = getattr(self.workspace_manager, "swarm_event_bus", None)
        if not bus:
            self.list_widget.addItem("No swarm event bus available.")
            return
        events = bus.get_history(limit=50)
        if not events:
            self.list_widget.addItem("No swarm events yet.")
            return
        for ev in reversed(events):
            ts = time.strftime("%H:%M:%S", time.localtime(ev.timestamp))
            summary = _format_event_data(ev.data)
            text = f"[{ts}] {ev.type} | ws={ev.workspace_id or '—'} | {summary}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, ev)
            self.list_widget.addItem(item)

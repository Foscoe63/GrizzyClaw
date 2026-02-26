"""Sub-agents dialog: list active and completed sub-agent runs, kill running ones."""

import logging
import time
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QHBoxLayout,
    QTabWidget,
    QWidget,
    QMessageBox,
)
from PyQt6.QtCore import Qt

if TYPE_CHECKING:
    from grizzyclaw.workspaces import WorkspaceManager


def _run_display_text(run) -> str:
    """One-line display for a subagent run."""
    ts = time.strftime("%H:%M:%S", time.localtime(run.created_at))
    label = (run.label or run.task_summary or "Sub-agent")[:40]
    status = getattr(run.status, "value", str(run.status))
    return f"[{ts}] {label} — {status} (run_id={run.run_id})"


class SubagentsDialog(QDialog):
    """Shows active and recently completed sub-agent runs; allows kill and refresh."""

    def __init__(self, workspace_manager: "WorkspaceManager", parent=None):
        super().__init__(parent)
        self.workspace_manager = workspace_manager
        self.setWindowTitle("Sub-agents")
        self.setMinimumSize(620, 480)
        self.setup_ui()
        self.refresh()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        self.hint = QLabel(
            "Sub-agents are background runs spawned by the agent (SPAWN_SUBAGENT). "
            "Active and completed runs from all workspaces are listed. "
            "Enable in Workspaces → Edit → Swarm / Sub-agents."
        )
        self.hint.setStyleSheet("color: gray; font-size: 11px;")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        tabs = QTabWidget()
        # Active tab
        active_widget = QWidget()
        active_layout = QVBoxLayout(active_widget)
        active_layout.addWidget(QLabel("Active runs"))
        self.active_list = QListWidget()
        self.active_list.setAlternatingRowColors(True)
        active_layout.addWidget(self.active_list)
        active_btn_layout = QHBoxLayout()
        kill_btn = QPushButton("Kill selected")
        kill_btn.clicked.connect(self.kill_selected)
        active_btn_layout.addWidget(kill_btn)
        active_btn_layout.addStretch()
        active_layout.addLayout(active_btn_layout)
        tabs.addTab(active_widget, "Active")

        # Completed tab
        completed_widget = QWidget()
        completed_layout = QVBoxLayout(completed_widget)
        completed_layout.addWidget(QLabel("Recently completed"))
        self.completed_list = QListWidget()
        self.completed_list.setAlternatingRowColors(True)
        completed_layout.addWidget(self.completed_list)
        tabs.addTab(completed_widget, "Completed")

        layout.addWidget(tabs)

        btn_layout = QHBoxLayout()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        btn_layout.addWidget(refresh_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        self.debug_label = QLabel("")
        self.debug_label.setStyleSheet("color: #888; font-size: 10px;")
        self.debug_label.setWordWrap(True)
        layout.addWidget(self.debug_label)

    def refresh(self):
        registry = self._get_registry()
        if not registry:
            self.active_list.clear()
            self.active_list.addItem("No subagent registry available.")
            self.completed_list.clear()
            self.completed_list.addItem("No subagent registry available.")
            self.debug_label.setText("Debug: no registry (chat agent or workspace_manager).")
            return

        # Debug: what the registry contains
        try:
            info = registry.get_debug_info()
        except Exception as e:
            info = {"error": str(e)}
        logger.info(
            "SubagentsDialog.refresh registry_id=%s total_runs=%d completed_order_len=%d running=%s",
            info.get("registry_id"), info.get("total_runs"), info.get("completed_order_len"),
            info.get("running_ids"),
        )
        self.debug_label.setText(
            "Debug: registry id=%s | total runs=%d | completed_order len=%d | running=%s"
            % (
                info.get("registry_id", "?"),
                info.get("total_runs", 0),
                info.get("completed_order_len", 0),
                info.get("running_count", 0),
            )
        )

        # Show runs from all workspaces so nothing is hidden by workspace mismatch
        # (e.g. agent.workspace_id may differ from active_workspace_id in some code paths)
        active = registry.list_active(workspace_id=None)
        completed = registry.list_recent_completed(limit=50, workspace_id=None)

        self.active_list.clear()
        if not active:
            self.active_list.addItem("No active sub-agents.")
        else:
            for run in active:
                item = QListWidgetItem(_run_display_text(run))
                item.setData(Qt.ItemDataRole.UserRole, run)
                self.active_list.addItem(item)

        self.completed_list.clear()
        if not completed:
            self.completed_list.addItem("No completed sub-agents yet.")
        else:
            for run in completed:
                text = _run_display_text(run)
                if run.result:
                    preview = (run.result[:80] + "…") if len(run.result) > 80 else run.result
                    text += f" — {preview}"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, run)
                self.completed_list.addItem(item)

    def _get_registry(self):
        """Use the exact registry the main window set for the current chat agent (single source of truth)."""
        parent = self.parent()
        if parent:
            r = getattr(parent, "_subagent_registry_for_ui", None)
            if r:
                return r
            if getattr(parent, "chat_widget", None) and getattr(parent.chat_widget, "agent", None):
                r = getattr(parent.chat_widget.agent, "subagent_registry", None)
                if r:
                    return r
        return getattr(self.workspace_manager, "subagent_registry", None) if self.workspace_manager else None

    def kill_selected(self):
        item = self.active_list.currentItem()
        if not item:
            QMessageBox.information(self, "Sub-agents", "Select an active run to kill.")
            return
        run = item.data(Qt.ItemDataRole.UserRole)
        if not run or not getattr(run, "run_id", None):
            return
        registry = self._get_registry()
        if not registry:
            return
        registry.cancel(run.run_id)
        self.refresh()
        QMessageBox.information(self, "Sub-agents", f"Cancel requested for run {run.run_id}. It will stop when it next checks.")

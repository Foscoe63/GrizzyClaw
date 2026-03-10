"""Scheduler dialog for managing scheduled tasks"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QMessageBox, QLineEdit,
    QFormLayout, QGroupBox, QComboBox, QTextEdit,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont


def _get_dialog_theme_colors(parent):
    """Get theme colors from parent (main window) settings."""
    theme = getattr(getattr(parent, "settings", None), "theme", "Light") if parent else "Light"
    dark_themes = ["Dark", "High Contrast Dark", "Dracula", "Monokai", "Nord", "Solarized Dark"]
    is_dark = theme in dark_themes
    if is_dark:
        return {
            "bg": "#1E1E1E", "fg": "#FFFFFF", "input_bg": "#3A3A3C",
            "accent": "#0A84FF", "summary_bg": "#2D2D2D", "border": "#3A3A3C",
        }
    return {
        "bg": "#FFFFFF", "fg": "#1C1C1E", "input_bg": "#FFFFFF",
        "accent": "#007AFF", "summary_bg": "#F5F5F7", "border": "#E5E5EA",
    }


class SchedulerDialog(QDialog):
    def __init__(self, agent, parent=None):
        super().__init__(parent)
        self.agent = agent
        self.setWindowTitle("⏰ Scheduled Tasks")
        self.setMinimumSize(700, 500)
        self._colors = _get_dialog_theme_colors(parent)
        self.setup_ui()
        self.refresh()

    def setup_ui(self):
        c = self._colors
        self.setStyleSheet(f"QDialog {{ background-color: {c['bg']}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Header
        header = QLabel("Scheduled Tasks")
        header.setFont(QFont("-apple-system", 18, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {c['fg']};")
        layout.addWidget(header)

        # Status summary
        self.status_label = QLabel("Loading...")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            f"font-size: 14px; padding: 10px; background: {c['summary_bg']}; "
            f"color: {c['fg']}; border-radius: 8px;"
        )
        layout.addWidget(self.status_label)

        # Task list
        self.task_list = QListWidget()
        self.task_list.setAlternatingRowColors(True)
        self.task_list.setStyleSheet(f"""
            QListWidget {{
                border: 1px solid {c['border']};
                border-radius: 8px;
                padding: 8px;
                background: {c['bg']};
                color: {c['fg']};
            }}
            QListWidget::item {{
                padding: 8px;
                border-radius: 4px;
            }}
            QListWidget::item:selected {{
                background-color: {c['accent']};
                color: white;
            }}
        """)
        self.task_list.currentItemChanged.connect(self._on_task_selection_changed)
        layout.addWidget(self.task_list)

        # Create new task section
        create_group = QGroupBox("Create New Task")
        create_layout = QFormLayout(create_group)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g., Daily Email Check")
        create_layout.addRow("Task Name:", self.name_input)

        # Cron preset dropdown + custom input
        cron_layout = QHBoxLayout()
        self.cron_preset = QComboBox()
        self.cron_preset.addItems([
            "Custom",
            "Every minute (*/1 * * * *)",
            "Every 5 minutes (*/5 * * * *)",
            "Every 30 minutes (*/30 * * * *)",
            "Every hour (0 * * * *)",
            "Every 2 hours (0 */2 * * *)",
            "Daily at 9 AM (0 9 * * *)",
            "Daily at noon (0 12 * * *)",
            "Daily at 6 PM (0 18 * * *)",
            "Weekly Monday 9 AM (0 9 * * 1)",
            "Monthly 1st at midnight (0 0 1 * *)",
        ])
        self.cron_preset.currentIndexChanged.connect(self.on_cron_preset_changed)
        cron_layout.addWidget(self.cron_preset)

        self.cron_input = QLineEdit()
        self.cron_input.setPlaceholderText("* * * * * (min hour day month weekday)")
        cron_layout.addWidget(self.cron_input)
        create_layout.addRow("Schedule:", cron_layout)

        self.message_input = QLineEdit()
        self.message_input.setPlaceholderText("What should happen when this task runs?")
        create_layout.addRow("Message:", self.message_input)

        layout.addWidget(create_group)

        # Buttons
        btn_layout = QHBoxLayout()

        create_btn = QPushButton("➕ Create Task")
        create_btn.clicked.connect(self.create_task)
        create_btn.setStyleSheet("""
            QPushButton {
                background-color: #34C759;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2DA44E;
            }
        """)
        btn_layout.addWidget(create_btn)

        self.save_btn = QPushButton("💾 Save")
        self.save_btn.setToolTip("Save name, schedule, and message for the selected task.")
        self.save_btn.clicked.connect(self.save_selected_task)
        self.save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {c['accent']};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                opacity: 0.9;
            }}
            QPushButton:disabled {{
                background-color: {c['border']};
                color: {c['fg']};
            }}
        """)
        btn_layout.addWidget(self.save_btn)

        self.start_btn = QPushButton("▶ Start")
        self.start_btn.setToolTip("Enable this task so it runs on schedule.")
        self.start_btn.clicked.connect(self.start_selected_task)
        self.start_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #34C759;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
            }}
            QPushButton:hover {{ opacity: 0.9; }}
            QPushButton:disabled {{ background-color: {c['border']}; color: {c['fg']}; }}
        """)
        btn_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("⏸ Stop")
        self.stop_btn.setToolTip("Pause this task so it does not run on schedule.")
        self.stop_btn.clicked.connect(self.stop_selected_task)
        self.stop_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #FF9500;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
            }}
            QPushButton:hover {{ opacity: 0.9; }}
            QPushButton:disabled {{ background-color: {c['border']}; color: {c['fg']}; }}
        """)
        btn_layout.addWidget(self.stop_btn)

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh)
        btn_layout.addWidget(refresh_btn)

        edit_btn = QPushButton("✏️ Edit")
        edit_btn.clicked.connect(self.edit_selected)
        btn_layout.addWidget(edit_btn)

        delete_btn = QPushButton("🗑️ Delete Selected")
        delete_btn.clicked.connect(self.delete_selected)
        delete_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF3B30;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #D62929;
            }
        """)
        btn_layout.addWidget(delete_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        self.save_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self._last_selected_task_id = None

    def _on_task_selection_changed(self, current, previous):
        """When a task is selected, load its name/cron/message into the form and update Start/Stop buttons."""
        if not current:
            self.save_btn.setEnabled(False)
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self._last_selected_task_id = None
            return
        task_id = current.data(Qt.ItemDataRole.UserRole)
        if not task_id:
            self.save_btn.setEnabled(False)
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self._last_selected_task_id = None
            return
        self._last_selected_task_id = task_id
        task = getattr(self.agent, "get_scheduler_task", lambda _: None)(task_id)
        if not task:
            self.save_btn.setEnabled(False)
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            return
        self.save_btn.setEnabled(True)
        enabled = task.get("enabled", True)
        # Keep both Start and Stop enabled when a task is selected so either click always works
        # (selection can be cleared when the button gets focus, so we rely on _last_selected_task_id in handlers)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.name_input.setText(task.get("name", ""))
        self.cron_input.setText(task.get("cron", ""))
        self.message_input.setText(task.get("message", ""))
        # Try to match cron to preset
        cron = (task.get("cron") or "").strip()
        presets = ["", "*/1 * * * *", "*/5 * * * *", "*/30 * * * *", "0 * * * *", "0 */2 * * *",
                   "0 9 * * *", "0 12 * * *", "0 18 * * *", "0 9 * * 1", "0 0 1 * *"]
        idx = presets.index(cron) if cron in presets else 0
        self.cron_preset.setCurrentIndex(idx)

    def save_selected_task(self):
        """Save the current form (name, cron, message) to the selected task."""
        current = self.task_list.currentItem()
        if not current:
            QMessageBox.warning(self, "No Selection", "Select a task to save.")
            return
        task_id = current.data(Qt.ItemDataRole.UserRole)
        if not task_id:
            return
        name = self.name_input.text().strip()
        cron = self.cron_input.text().strip()
        message = self.message_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Enter a task name.")
            return
        if not cron:
            QMessageBox.warning(self, "Missing Schedule", "Enter a cron expression.")
            return
        if not message:
            QMessageBox.warning(self, "Missing Message", "Enter a task message.")
            return
        try:
            result = self.agent.edit_scheduler_task_sync(
                task_id,
                name=name,
                cron=cron,
                message=message,
            )
            if result and "✅" in result:
                QMessageBox.information(self, "Saved", result)
                self.refresh()
            else:
                QMessageBox.warning(self, "Error", result or "Save failed.")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def start_selected_task(self):
        """Enable the selected task so it runs on schedule."""
        # Use currentItem() first; fall back to last selected (clicking the button can clear list selection)
        current = self.task_list.currentItem()
        task_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        if not task_id:
            task_id = getattr(self, "_last_selected_task_id", None)
        if not task_id:
            QMessageBox.warning(self, "No Selection", "Select a task first, then click Start.")
            return
        try:
            result = self.agent.enable_scheduler_task_sync(task_id)
            if result and "✅" in result:
                QMessageBox.information(self, "Started", result)
                self.refresh()
                self._reselect_task(task_id)
            else:
                QMessageBox.warning(self, "Error", result or "Could not start task.")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def stop_selected_task(self):
        """Disable the selected task so it does not run on schedule."""
        current = self.task_list.currentItem()
        task_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        if not task_id:
            task_id = getattr(self, "_last_selected_task_id", None)
        if not task_id:
            QMessageBox.warning(self, "No Selection", "Select a task first, then click Stop.")
            return
        try:
            result = self.agent.disable_scheduler_task_sync(task_id)
            if result and "✅" in result:
                QMessageBox.information(self, "Stopped", result)
                self.refresh()
                self._reselect_task(task_id)
            else:
                QMessageBox.warning(self, "Error", result or "Could not stop task.")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _reselect_task(self, task_id: str):
        """After refresh, re-select the item with the given task_id so Start/Stop buttons stay correct."""
        for i in range(self.task_list.count()):
            item = self.task_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == task_id:
                self.task_list.setCurrentItem(item)
                break

    def on_cron_preset_changed(self, index):
        """Update cron input when preset is selected"""
        presets = {
            0: "",  # Custom
            1: "*/1 * * * *",
            2: "*/5 * * * *",
            3: "*/30 * * * *",
            4: "0 * * * *",
            5: "0 */2 * * *",
            6: "0 9 * * *",
            7: "0 12 * * *",
            8: "0 18 * * *",
            9: "0 9 * * 1",
            10: "0 0 1 * *",
        }
        if index > 0:
            self.cron_input.setText(presets.get(index, ""))

    def refresh(self):
        """Refresh the task list (reload from disk so tasks created in chat are shown)."""
        try:
            self.agent.reload_scheduled_tasks_from_disk()
            # Scheduler loop is run by the dedicated GUI scheduler thread; no need to start it here
            stats = self.agent.get_scheduler_status()
            total = stats.get("total_tasks", 0)
            enabled = stats.get("enabled", 0)
            running = "✅ Running" if stats.get("running") else "⏸️ Stopped"

            self.status_label.setText(
                f"Total Tasks: {total} | Enabled: {enabled} | Status: {running}"
            )

            self.task_list.clear()
            for task in stats.get("tasks", []):
                status_icon = "✅" if task["enabled"] else "❌"
                next_run = task["next_run"][:16] if task["next_run"] else "N/A"
                item_text = (
                    f"{status_icon} {task['name']} | "
                    f"Cron: {task['cron']} | "
                    f"Next: {next_run} | "
                    f"Runs: {task['run_count']}"
                )
                item = QListWidgetItem(item_text)
                item.setData(Qt.ItemDataRole.UserRole, task["id"])
                self.task_list.addItem(item)

        except Exception as e:
            self.status_label.setText(f"Error: {str(e)}")
            self.task_list.clear()

    def create_task(self):
        """Create a new scheduled task"""
        name = self.name_input.text().strip()
        cron = self.cron_input.text().strip()
        message = self.message_input.text().strip()

        if not name:
            QMessageBox.warning(self, "Missing Name", "Please enter a task name.")
            return
        if not cron:
            QMessageBox.warning(self, "Missing Schedule", "Please enter a cron expression.")
            return
        if not message:
            QMessageBox.warning(self, "Missing Message", "Please enter a task message.")
            return

        try:
            import asyncio

            async def create():
                return await self.agent._execute_schedule_action(
                    "gui_user",
                    {
                        "action": "create",
                        "task": {"name": name, "cron": cron, "message": message},
                    },
                )

            from grizzyclaw.utils.async_runner import run_async
            result = run_async(create())

            if "✅" in result:
                QMessageBox.information(self, "Task Created", result)
                self.name_input.clear()
                self.cron_input.clear()
                self.message_input.clear()
                self.cron_preset.setCurrentIndex(0)
                self.refresh()
            else:
                QMessageBox.warning(self, "Error", result)

        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def delete_selected(self):
        """Delete the selected task"""
        current = self.task_list.currentItem()
        if not current:
            QMessageBox.warning(self, "No Selection", "Select a task to delete.")
            return

        task_id = current.data(Qt.ItemDataRole.UserRole)
        if not task_id:
            return

        reply = QMessageBox.question(
            self, "Confirm Delete", f"Delete task '{task_id}'?"
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                import asyncio

                async def delete():
                    return await self.agent._execute_schedule_action(
                        "gui_user", {"action": "delete", "task_id": task_id}
                    )

                from grizzyclaw.utils.async_runner import run_async
                result = run_async(delete())

                if "✅" in result:
                    QMessageBox.information(self, "Deleted", result)
                    self.refresh()
                else:
                    QMessageBox.warning(self, "Error", result)

            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))

    def edit_selected(self):
        """Edit the selected task (cron, message, name)"""
        current = self.task_list.currentItem()
        if not current:
            QMessageBox.warning(self, "No Selection", "Select a task to edit.")
            return
        task_id = current.data(Qt.ItemDataRole.UserRole)
        if not task_id:
            return
        task = getattr(self.agent, "get_scheduler_task", lambda _: None)(task_id)
        if not task:
            QMessageBox.warning(self, "Error", "Task not found or no details available.")
            return
        d = QDialog(self)
        d.setWindowTitle("Edit Task")
        layout = QVBoxLayout(d)
        form = QFormLayout()
        name_edit = QLineEdit()
        name_edit.setText(task.get("name", ""))
        form.addRow("Name:", name_edit)
        cron_edit = QLineEdit()
        cron_edit.setPlaceholderText("* * * * *")
        cron_edit.setText(task.get("cron", ""))
        form.addRow("Cron:", cron_edit)
        msg_edit = QLineEdit()
        msg_edit.setText(task.get("message", ""))
        msg_edit.setPlaceholderText("Task message")
        form.addRow("Message:", msg_edit)
        layout.addLayout(form)
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("Save")
        ok_btn.clicked.connect(d.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(d.reject)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        if d.exec() == QDialog.DialogCode.Accepted:
            result = self.agent.edit_scheduler_task_sync(
                task_id,
                cron=cron_edit.text().strip() or None,
                message=msg_edit.text().strip() or None,
                name=name_edit.text().strip() or None,
            )
            if "✅" in result:
                QMessageBox.information(self, "Updated", result)
                self.refresh()
            else:
                QMessageBox.warning(self, "Error", result)

"""Triggers dialog for managing automation trigger rules"""

import uuid

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from grizzyclaw.automation.triggers import (
    TriggerAction,
    TriggerCondition,
    TriggerRule,
    load_triggers,
    save_triggers,
)


def _get_dialog_theme_colors(parent):
    """Get theme colors from parent (main window) settings."""
    theme = (
        getattr(getattr(parent, "settings", None), "theme", "Light")
        if parent
        else "Light"
    )
    dark_themes = [
        "Dark",
        "High Contrast Dark",
        "Dracula",
        "Monokai",
        "Nord",
        "Solarized Dark",
    ]
    is_dark = theme in dark_themes
    if is_dark:
        return {
            "bg": "#1E1E1E",
            "fg": "#FFFFFF",
            "input_bg": "#3A3A3C",
            "accent": "#0A84FF",
            "summary_bg": "#2D2D2D",
            "border": "#3A3A3C",
        }
    return {
        "bg": "#FFFFFF",
        "fg": "#1C1C1E",
        "input_bg": "#FFFFFF",
        "accent": "#007AFF",
        "summary_bg": "#F5F5F7",
        "border": "#E5E5EA",
    }


class TriggersDialog(QDialog):
    """Manage automation trigger rules (message/webhook/schedule)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚡ Automation Triggers")
        self.setMinimumSize(650, 500)
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
        header = QLabel("Automation Triggers")
        header.setFont(QFont("-apple-system", 18, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {c['fg']};")
        layout.addWidget(header)

        hint = QLabel(
            "Triggers run actions when events match conditions (e.g. message contains 'urgent' → webhook)"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size: 12px; color: {c['fg']}; opacity: 0.8;")
        layout.addWidget(hint)

        # Trigger list
        self.trigger_list = QListWidget()
        self.trigger_list.setAlternatingRowColors(True)
        self.trigger_list.setStyleSheet(
            f"""
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
        """
        )
        layout.addWidget(self.trigger_list)

        # Create new trigger section
        create_group = QGroupBox("Create New Trigger")
        create_group.setStyleSheet(
            f"QGroupBox {{ font-weight: 600; border: 1px solid {c['border']}; "
            f"border-radius: 6px; padding: 12px; margin-top: 8px; }}"
        )
        create_layout = QFormLayout(create_group)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g., Urgent Alert")
        self.name_input.setFixedHeight(32)
        create_layout.addRow("Name:", self.name_input)

        self.event_combo = QComboBox()
        self.event_combo.addItems(["message", "webhook", "schedule"])
        self.event_combo.setFixedHeight(32)
        create_layout.addRow("Event:", self.event_combo)

        cond_layout = QHBoxLayout()
        self.cond_type_combo = QComboBox()
        self.cond_type_combo.addItems(["contains", "matches", "equals"])
        self.cond_type_combo.setFixedHeight(32)
        cond_layout.addWidget(self.cond_type_combo)

        self.cond_value_input = QLineEdit()
        self.cond_value_input.setPlaceholderText("Pattern or regex (empty = always)")
        self.cond_value_input.setFixedHeight(32)
        cond_layout.addWidget(self.cond_value_input)
        create_layout.addRow("Condition:", cond_layout)

        action_layout = QHBoxLayout()
        self.action_type_combo = QComboBox()
        self.action_type_combo.addItems(["agent_message", "webhook", "notify"])
        self.action_type_combo.setFixedHeight(32)
        action_layout.addWidget(self.action_type_combo)

        self.action_config_input = QLineEdit()
        self.action_config_input.setPlaceholderText(
            "For webhook: {\"url\": \"https://...\"}"
        )
        self.action_config_input.setFixedHeight(32)
        action_layout.addWidget(self.action_config_input)
        create_layout.addRow("Action config:", action_layout)

        layout.addWidget(create_group)

        # Buttons
        btn_layout = QHBoxLayout()

        create_btn = QPushButton("➕ Add Trigger")
        create_btn.clicked.connect(self.add_trigger)
        create_btn.setStyleSheet(
            """
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
        """
        )
        btn_layout.addWidget(create_btn)

        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh)
        btn_layout.addWidget(refresh_btn)

        delete_btn = QPushButton("🗑️ Delete Selected")
        delete_btn.clicked.connect(self.delete_selected)
        delete_btn.setStyleSheet(
            """
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
        """
        )
        btn_layout.addWidget(delete_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def refresh(self):
        """Reload triggers from disk and update list."""
        rules = load_triggers()
        self.trigger_list.clear()
        for r in rules:
            status = "✅" if r.enabled else "❌"
            cond_str = ""
            if r.condition:
                cond_str = f" {r.condition.type}={r.condition.value}"
            item_text = f"{status} {r.name} | {r.event}{cond_str} → {r.action.type}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, r.id)
            self.trigger_list.addItem(item)

    def add_trigger(self):
        """Add a new trigger rule."""
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Please enter a trigger name.")
            return

        event = self.event_combo.currentText()
        cond_type = self.cond_type_combo.currentText()
        cond_value = self.cond_value_input.text().strip()

        condition = None
        if cond_value:
            condition = TriggerCondition(type=cond_type, value=cond_value)

        action_type = self.action_type_combo.currentText()
        config_str = self.action_config_input.text().strip()
        config = {}
        if config_str:
            try:
                import json

                config = json.loads(config_str)
            except Exception:
                QMessageBox.warning(
                    self,
                    "Invalid JSON",
                    "Action config must be valid JSON (e.g. {\"url\": \"https://...\"})",
                )
                return

        rule = TriggerRule(
            id=str(uuid.uuid4())[:8],
            name=name,
            enabled=True,
            event=event,
            condition=condition,
            action=TriggerAction(type=action_type, config=config),
        )

        rules = load_triggers()
        rules.append(rule)
        if save_triggers(rules):
            QMessageBox.information(self, "Added", f"Trigger '{name}' added.")
            self.name_input.clear()
            self.cond_value_input.clear()
            self.action_config_input.clear()
            self.refresh()
        else:
            QMessageBox.warning(self, "Error", "Failed to save triggers.")

    def delete_selected(self):
        """Delete the selected trigger."""
        current = self.trigger_list.currentItem()
        if not current:
            QMessageBox.warning(
                self, "No Selection", "Select a trigger to delete."
            )
            return

        rule_id = current.data(Qt.ItemDataRole.UserRole)
        if not rule_id:
            return

        reply = QMessageBox.question(
            self, "Confirm Delete", f"Delete trigger '{current.text()}'?"
        )
        if reply == QMessageBox.StandardButton.Yes:
            rules = load_triggers()
            rules = [r for r in rules if r.id != rule_id]
            if save_triggers(rules):
                QMessageBox.information(self, "Deleted", "Trigger removed.")
                self.refresh()
            else:
                QMessageBox.warning(self, "Error", "Failed to save.")

from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QMessageBox)
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


class MemoryDialog(QDialog):
    def __init__(self, agent, user_id, parent=None):
        super().__init__(parent)
        self.agent = agent
        self.user_id = user_id
        self.memories = []
        self.setWindowTitle("🧠 Memory")
        self.setMinimumSize(600, 500)
        self._colors = _get_dialog_theme_colors(parent)
        self.setup_ui()
        self.refresh()

    def setup_ui(self):
        c = self._colors
        self.setStyleSheet(f"QDialog {{ background-color: {c['bg']}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Summary
        self.summary_label = QLabel("Loading...")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(
            f"font-size: 16px; font-weight: bold; padding: 10px; "
            f"background: {c['summary_bg']}; color: {c['fg']}; border-radius: 8px;"
        )
        layout.addWidget(self.summary_label)

        # Memories list
        self.memories_list = QListWidget()
        self.memories_list.setAlternatingRowColors(True)
        self.memories_list.setStyleSheet(f"""
            QListWidget {{ background: {c['bg']}; color: {c['fg']}; border: 1px solid {c['border']}; border-radius: 8px; }}
            QListWidget::item:selected {{ background-color: {c['accent']}; color: white; }}
        """)
        layout.addWidget(self.memories_list)

        # Buttons
        btn_layout = QHBoxLayout()
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh)
        btn_layout.addWidget(refresh_btn)

        delete_btn = QPushButton("🗑️ Delete Selected")
        delete_btn.clicked.connect(self.delete_selected)
        btn_layout.addWidget(delete_btn)

        clear_btn = QPushButton("🚫 Clear All")
        clear_btn.clicked.connect(self.clear_all)
        btn_layout.addWidget(clear_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def refresh(self):
        try:
            summary = self.agent.get_user_memory_sync(self.user_id)
            total = summary.get('total_items', 0)
            recent_count = len(summary.get('recent_items', []))
            self.summary_label.setText(f"Total Memories: {total} | Recent: {recent_count} | User: {self.user_id}")

            self.memories = self.agent.list_memories_sync(self.user_id, 50)
            self.memories_list.clear()
            for mem in self.memories:
                created = mem.get('created_at', 'Unknown')
                if hasattr(created, 'strftime'):
                    created_str = created.strftime('%m/%d %H:%M')
                else:
                    created_str = str(created)[:16]
                content = mem.get('content', '')[:120] + '...' if len(mem.get('content', '')) > 120 else mem.get('content', '')
                category = mem.get('category', 'conversation')
                item_text = f"{created_str} | {category} | {content}"
                item = QListWidgetItem(item_text)
                item.setData(Qt.ItemDataRole.UserRole, mem.get('id'))
                self.memories_list.addItem(item)
        except Exception as e:
            self.summary_label.setText(f"Error loading memory: {str(e)}")
            self.memories_list.clear()

    def delete_selected(self):
        current = self.memories_list.currentItem()
        if not current:
            QMessageBox.warning(self, "No Selection", "Select a memory to delete.")
            return
        item_id = current.data(Qt.ItemDataRole.UserRole)
        if not item_id:
            return
        reply = QMessageBox.question(self, "Confirm Delete", "Delete this memory?")
        if reply == QMessageBox.StandardButton.Yes:
            try:
                success = self.agent.delete_memory_sync(item_id)
                if success:
                    self.refresh()
                    QMessageBox.information(self, "Deleted", "Memory deleted.")
                else:
                    QMessageBox.warning(self, "Error", "Delete failed.")
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))

    def clear_all(self):
        reply = QMessageBox.question(self, "Confirm Clear", "Clear ALL memories for this user?")
        if reply == QMessageBox.StandardButton.Yes:
            try:
                # Get all ids
                all_mem = self.agent.list_memories_sync(self.user_id, 1000)
                deleted = 0
                for mem in all_mem:
                    if self.agent.delete_memory_sync(mem['id']):
                        deleted += 1
                self.refresh()
                QMessageBox.information(self, "Cleared", f"Cleared {deleted} memories.")
            except Exception as e:
                QMessageBox.warning(self, "Error", str(e))

"""Conversation history: list current session, Clear, Load from disk."""

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont


class ConversationHistoryDialog(QDialog):
    """Show current session summary (messages, ~tokens) and Clear / Load from disk."""

    def __init__(self, agent, user_id: str, on_clear, on_load, parent=None):
        super().__init__(parent)
        self.agent = agent
        self.user_id = user_id
        self.on_clear = on_clear
        self.on_load = on_load
        self.setWindowTitle("Conversation history")
        self.setMinimumWidth(360)
        self.setup_ui()
        self.refresh_summary()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        self.summary_label = QLabel("Loading...")
        self.summary_label.setWordWrap(True)
        self.summary_label.setFont(QFont("-apple-system", 14))
        layout.addWidget(self.summary_label)
        hint = QLabel("Clear starts a new conversation. Load restores the last saved session from disk.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 12px;")
        layout.addWidget(hint)
        btn_layout = QHBoxLayout()
        clear_btn = QPushButton("Clear (new chat)")
        clear_btn.clicked.connect(self._do_clear)
        btn_layout.addWidget(clear_btn)
        load_btn = QPushButton("Load from disk")
        load_btn.clicked.connect(self._do_load)
        btn_layout.addWidget(load_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def refresh_summary(self):
        if not self.agent:
            self.summary_label.setText("No agent.")
            return
        summary = getattr(self.agent, "get_session_summary", lambda _: {"messages": 0, "approx_tokens": 0})(
            self.user_id
        )
        n = summary.get("messages", 0)
        tok = summary.get("approx_tokens", 0)
        self.summary_label.setText(f"Current session: {n} messages, ~{tok // 1000}k tokens")

    def _do_clear(self):
        if self.on_clear:
            self.on_clear()
        self.refresh_summary()
        QMessageBox.information(self, "Cleared", "Conversation cleared. You can start a new chat.")

    def _do_load(self):
        if self.on_load:
            self.on_load()
        self.refresh_summary()
        QMessageBox.information(self, "Loaded", "Session restored from disk (if one was saved).")

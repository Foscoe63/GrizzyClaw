"""Sessions dialog - list, manage, and send messages between concurrent sessions/agents"""

import asyncio
import json
import logging
from typing import Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextEdit,
    QMessageBox,
    QSplitter,
)

logger = logging.getLogger(__name__)

GATEWAY_WS = "ws://127.0.0.1:18789"


def _get_dialog_theme_colors(parent):
    theme = getattr(getattr(parent, "settings", None), "theme", "Light") if parent else "Light"
    dark = theme in ("Dark", "High Contrast Dark", "Dracula", "Monokai", "Nord", "Solarized Dark")
    if dark:
        return {"bg": "#1E1E1E", "fg": "#FFFFFF", "accent": "#0A84FF", "border": "#3A3A3C"}
    return {"bg": "#FFFFFF", "fg": "#1C1C1E", "accent": "#007AFF", "border": "#E5E5EA"}


class SessionsDialog(QDialog):
    """Manage multi-agent sessions: list, view history, send messages between sessions."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sessions")
        self.setMinimumSize(700, 500)
        self._colors = _get_dialog_theme_colors(parent)
        self._ws = None
        self._loop = None
        self.setup_ui()

    def setup_ui(self):
        c = self._colors
        self.setStyleSheet(f"QDialog {{ background-color: {c['bg']}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        header = QLabel("Multi-Agent Sessions")
        header.setFont(QFont("-apple-system", 18, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {c['fg']};")
        layout.addWidget(header)

        hint = QLabel(
            "List sessions, view history, and send messages. Requires daemon running (Gateway on ws://127.0.0.1:18789)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size: 12px; color: {c['fg']}; opacity: 0.8;")
        layout.addWidget(hint)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Session list
        list_widget = QWidget()
        list_layout = QVBoxLayout(list_widget)
        list_layout.addWidget(QLabel("Sessions"))
        self.session_list = QListWidget()
        self.session_list.setMinimumWidth(200)
        self.session_list.itemSelectionChanged.connect(self._on_session_selected)
        list_layout.addWidget(self.session_list)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_sessions)
        list_layout.addWidget(refresh_btn)
        splitter.addWidget(list_widget)

        # History + send
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(QLabel("History"))
        self.history_text = QTextEdit()
        self.history_text.setReadOnly(True)
        self.history_text.setPlaceholderText("Select a session to view history")
        right_layout.addWidget(self.history_text)
        send_row = QHBoxLayout()
        self.message_input = QTextEdit()
        self.message_input.setMaximumHeight(80)
        self.message_input.setPlaceholderText("Message to send to selected session...")
        send_row.addWidget(self.message_input)
        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self._send_message)
        send_row.addWidget(send_btn)
        right_layout.addLayout(send_row)
        splitter.addWidget(right_widget)

        splitter.setSizes([250, 450])
        layout.addWidget(splitter)

        self._refresh_sessions()

    def _refresh_sessions(self):
        """Fetch sessions from gateway."""
        try:
            import websockets
        except ImportError:
            QMessageBox.warning(self, "Error", "websockets package required for sessions.")
            return
        asyncio.run(self._fetch_sessions())

    async def _fetch_sessions(self):
        try:
            import websockets
            async with websockets.connect(GATEWAY_WS, close_timeout=5) as ws:
                await ws.send(json.dumps({"type": "get_sessions"}))
                for _ in range(5):
                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                    data = json.loads(msg)
                    if data.get("type") == "sessions":
                        self._populate_sessions(data.get("sessions", []))
                        return
                    if data.get("type") == "error":
                        break
                self.session_list.clear()
        except Exception as e:
            logger.debug(f"Could not fetch sessions: {e}")
            self.session_list.clear()
            self.session_list.addItem(QListWidgetItem("(Daemon not running or unreachable)"))

    def _populate_sessions(self, sessions: list):
        self.session_list.clear()
        for s in sessions:
            sid = s.get("session_id", "?")
            uid = s.get("user_id", "?")
            item = QListWidgetItem(f"{sid} ({uid})")
            item.setData(Qt.ItemDataRole.UserRole, sid)
            self.session_list.addItem(item)

    def _on_session_selected(self):
        current = self.session_list.currentItem()
        if not current:
            return
        sid = current.data(Qt.ItemDataRole.UserRole)
        if sid:
            asyncio.run(self._fetch_history(sid))

    async def _fetch_history(self, session_id: str):
        try:
            import websockets
            async with websockets.connect(GATEWAY_WS, close_timeout=5) as ws:
                await ws.send(json.dumps({"type": "sessions_history", "session_id": session_id}))
                for _ in range(5):
                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                    data = json.loads(msg)
                    if data.get("type") == "session_history":
                        history = data.get("history", [])
                        lines = []
                        for h in history:
                            role = h.get("role", "?")
                            content = (h.get("content", "") or "")[:500]
                            lines.append(f"{role}: {content}")
                        self.history_text.setText("\n\n".join(lines) if lines else "No history")
                        return
                    if data.get("type") == "error":
                        self.history_text.setText(data.get("error", "Error"))
                        return
                self.history_text.setText("No response")
        except Exception as e:
            self.history_text.setText(f"Error: {e}")

    def _send_message(self):
        current = self.session_list.currentItem()
        if not current:
            QMessageBox.warning(self, "No Session", "Select a session first.")
            return
        sid = current.data(Qt.ItemDataRole.UserRole)
        msg = self.message_input.toPlainText().strip()
        if not msg:
            return
        try:
            asyncio.run(self._do_send(sid, msg))
            self.message_input.clear()
            self._on_session_selected()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    async def _do_send(self, session_id: str, message: str):
        import websockets
        async with websockets.connect(GATEWAY_WS, close_timeout=10) as ws:
            await ws.send(json.dumps({
                "type": "sessions_send",
                "session_id": session_id,
                "user_id": "gui_user",
                "message": message,
            }))
            # Wait for response
            for _ in range(30):
                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                data = json.loads(msg)
                if data.get("type") == "sessions_send_result":
                    break

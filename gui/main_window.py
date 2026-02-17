import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QLineEdit, QPushButton, QLabel, QSystemTrayIcon,
    QMenu, QMenuBar, QMessageBox, QSplitter, QListWidget, QListWidgetItem,
    QFrame, QScrollArea, QToolBar, QStatusBar, QSizePolicy,
    QFileDialog,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize, QMimeData
from PyQt6.QtGui import QAction, QIcon, QFont, QPalette, QColor, QKeySequence, QShortcut, QDragEnterEvent, QDropEvent


from grizzyclaw.config import Settings, get_config_path
from grizzyclaw.agent.core import AgentCore
from grizzyclaw.channels.telegram import TelegramChannel
from grizzyclaw.gui.settings_dialog import SettingsDialog, _sanitize_telegram_token
from .memory_dialog import MemoryDialog
from .scheduler_dialog import SchedulerDialog
from .browser_dialog import BrowserDialog
from .workspace_dialog import WorkspaceDialog
from .canvas_widget import CanvasWidget
from grizzyclaw.workspaces import WorkspaceManager


class TelegramStartWorker(QThread):
    """Runs Telegram bot in background; emits failed if start throws."""
    failed = pyqtSignal(str)

    def __init__(self, bot, parent=None):
        super().__init__(parent)
        self.bot = bot

    def run(self):
        try:
            asyncio.run(self.bot.start())
        except Exception as e:
            self.failed.emit(str(e))


class TTSWorker(QThread):
    """Speak text using TTS in background."""
    finished = pyqtSignal(bool)

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.text = text

    def run(self):
        try:
            from grizzyclaw.utils.tts import speak_text
            ok = speak_text(self.text)
            self.finished.emit(ok)
        except Exception:
            self.finished.emit(False)


class MessageWorker(QThread):
    """Worker thread to handle async agent processing."""
    message_ready = pyqtSignal(str)
    chunk_ready = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, agent, user_id, message, images=None, audio_path=None):
        super().__init__()
        self.agent = agent
        self.user_id = user_id
        self.message = message
        self.images = images or []
        self.audio_path = audio_path

    def run(self):
        """Run the async processing in a separate thread"""
        try:
            import asyncio
            response_text = asyncio.run(self._process_message())
            self.message_ready.emit(response_text)
        except Exception as e:
            self.error_occurred.emit(f"Error: {str(e)}")

    async def _process_message(self):
        """Process the message asynchronously, streaming each chunk."""
        response_text = ""
        kwargs = {"images": self.images}
        if self.audio_path:
            kwargs["audio_path"] = self.audio_path
        async for chunk in self.agent.process_message(
            self.user_id, self.message, **kwargs
        ):
            response_text += chunk
            self.chunk_ready.emit(chunk)
        return response_text


class MessageBubble(QFrame):
    speak_requested = pyqtSignal(str)

    def __init__(self, text, is_user=True, parent=None, is_dark=False):
        super().__init__(parent)
        self.is_user = is_user
        self.is_dark = is_dark
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAutoFillBackground(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(8)

        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setFont(QFont("-apple-system", 14))
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.update_style()

        if is_user:
            layout.addStretch()
            layout.addWidget(self.label, alignment=Qt.AlignmentFlag.AlignRight)
        else:
            layout.addWidget(self.label, alignment=Qt.AlignmentFlag.AlignLeft)
            self.speak_btn = QPushButton("🔊")
            self.speak_btn.setToolTip("Speak response")
            self.speak_btn.setFixedSize(28, 28)
            self.speak_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.speak_btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: none;
                    border-radius: 14px;
                }
                QPushButton:hover { background: rgba(0,0,0,0.1); }
            """)
            self.speak_btn.clicked.connect(lambda: self.speak_requested.emit(self.label.text()))
            layout.addWidget(self.speak_btn, alignment=Qt.AlignmentFlag.AlignLeft)
            layout.addStretch()

        self.setMaximumWidth(750)
        self.label.setMaximumWidth(600)
    
    def update_style(self, is_dark=None):
        if is_dark is not None:
            self.is_dark = is_dark
        
        if self.is_user:
            # User messages - blue in both themes
            self.label.setStyleSheet("""
                QLabel {
                    background-color: #007AFF;
                    color: white;
                    padding: 10px 14px;
                    border-radius: 18px;
                    border-bottom-right-radius: 4px;
                }
            """)
        else:
            # Assistant messages - adapt to theme
            if self.is_dark:
                self.label.setStyleSheet("""
                    QLabel {
                        background-color: #3A3A3C;
                        color: #FFFFFF;
                        padding: 10px 14px;
                        border-radius: 18px;
                        border-bottom-left-radius: 4px;
                    }
                """)
            else:
                self.label.setStyleSheet("""
                    QLabel {
                        background-color: #E9E9EB;
                        color: #000000;
                        padding: 10px 14px;
                        border-radius: 18px;
                        border-bottom-left-radius: 4px;
                    }
                """)


class ChatWidget(QWidget):
    message_received = pyqtSignal(str, bool)
    image_attached = pyqtSignal(str)  # path to display in canvas

    def __init__(self, agent, parent=None):
        super().__init__(parent)
        self.agent = agent
        self.user_id = "gui_user"
        self.current_conversation = []
        self.is_dark = False
        self.setup_ui()
        self.message_received.connect(self.add_message)
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(0)
        
        # Header with better spacing
        header_container = QWidget()
        header_layout = QHBoxLayout(header_container)
        header_layout.setContentsMargins(0, 0, 0, 16)
        
        self.header_label = QLabel("Chat")
        self.header_label.setFont(QFont("-apple-system", 24, QFont.Weight.Bold))
        self.header_label.setStyleSheet("color: #1C1C1E;")
        header_layout.addWidget(self.header_label)
        header_layout.addStretch()

        self.new_chat_btn = QPushButton("New Chat")
        self.new_chat_btn.setToolTip("Start a new conversation (Ctrl+N)")
        self.new_chat_btn.setFont(QFont("-apple-system", 13))
        self.new_chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_chat_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #007AFF;
                border: none;
                padding: 4px 12px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #E5E5EA;
            }
        """)
        self.new_chat_btn.clicked.connect(self._new_chat)
        header_layout.addWidget(self.new_chat_btn)

        self.export_btn = QPushButton("Export")
        self.export_btn.setToolTip("Export conversation")
        self.export_btn.setFont(QFont("-apple-system", 13))
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #007AFF;
                border: none;
                padding: 4px 12px;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #E5E5EA;
            }
        """)
        self.export_btn.clicked.connect(self._export_conversation)
        header_layout.addWidget(self.export_btn)

        layout.addWidget(header_container)
        
        # Separator line (theme border applied in update_theme)
        self.separator_top = QFrame()
        self.separator_top.setFrameShape(QFrame.Shape.HLine)
        self.separator_top.setStyleSheet("background-color: #E5E5EA; max-height: 1px;")
        self.separator_top.setFixedHeight(1)
        layout.addWidget(self.separator_top)
        
        # Chat area with proper spacing
        layout.addSpacing(16)
        
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.chat_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #C7C7CC;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: #8E8E93;
            }
        """)
        
        self.chat_container = QWidget()
        self.chat_container.setStyleSheet("background: transparent;")
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.chat_layout.setSpacing(8)
        self.chat_layout.setContentsMargins(0, 0, 12, 0)
        self.chat_layout.addStretch()

        # Empty state welcome message
        self.empty_state = QLabel(
            "Welcome! Start a conversation by typing a message below.\n\n"
            "I can help with questions, remember things for you, schedule tasks, and browse the web."
        )
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.setFont(QFont("-apple-system", 15))
        self.empty_state.setStyleSheet("color: #8E8E93; padding: 40px;")
        self.empty_state.setWordWrap(True)
        self.chat_layout.insertWidget(0, self.empty_state)
        
        self.chat_scroll.setWidget(self.chat_container)
        self._user_near_bottom = True
        self.chat_scroll.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        layout.addWidget(self.chat_scroll, 1)
        
        layout.addSpacing(16)
        
        # Separator line (theme border applied in update_theme)
        self.separator_bottom = QFrame()
        self.separator_bottom.setFrameShape(QFrame.Shape.HLine)
        self.separator_bottom.setStyleSheet("background-color: #E5E5EA; max-height: 1px;")
        self.separator_bottom.setFixedHeight(1)
        layout.addWidget(self.separator_bottom)
        
        layout.addSpacing(16)
        
        # Input area with better styling
        input_container = QWidget()
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(12)
        
        self.pending_images: list[str] = []
        self.pending_audio: Optional[str] = None

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Type your message or attach an image/audio...")
        self.input_field.setFont(QFont("-apple-system", 14))
        self.input_field.setFixedHeight(44)
        self.input_field.setStyleSheet("""
            QLineEdit {
                padding: 0 16px;
                border: 1px solid #D1D1D6;
                border-radius: 22px;
                background: #FFFFFF;
                color: #1C1C1E;
            }
            QLineEdit:focus {
                border-color: #007AFF;
                border-width: 2px;
            }
            QLineEdit::placeholder {
                color: #8E8E93;
            }
        """)
        self.input_field.returnPressed.connect(self.send_message)
        self.input_field.setAcceptDrops(True)
        self.input_field.dragEnterEvent = self._input_drag_enter
        self.input_field.dropEvent = self._input_drop

        self.attach_btn = QPushButton("📎")
        self.attach_btn.setToolTip("Attach image or audio (or drag and drop)")
        self.attach_btn.setFont(QFont("-apple-system", 14))
        self.attach_btn.setFixedSize(44, 44)
        self.attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #8E8E93;
                border: none;
                border-radius: 22px;
                text-align: center;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #E5E5EA;
                color: #1C1C1E;
            }
        """)
        self.attach_btn.clicked.connect(self._attach_file)

        self.mic_btn = QPushButton("🎤")
        self.mic_btn.setToolTip("Attach voice/audio message")
        self.mic_btn.setFont(QFont("-apple-system", 14))
        self.mic_btn.setFixedSize(44, 44)
        self.mic_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mic_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #8E8E93;
                border: none;
                border-radius: 22px;
                text-align: center;
                padding: 10px;
            }
            QPushButton:hover {
                background-color: #E5E5EA;
                color: #1C1C1E;
            }
        """)
        self.mic_btn.clicked.connect(self._attach_audio)

        self.send_btn = QPushButton("Send")
        self.send_btn.setFont(QFont("-apple-system", 14, QFont.Weight.Medium))
        self.send_btn.setFixedSize(80, 44)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #007AFF;
                color: white;
                border: none;
                border-radius: 22px;
                text-align: center;
                padding: 0;
            }
            QPushButton:hover {
                background-color: #0051D5;
            }
            QPushButton:pressed {
                background-color: #003BB3;
            }
            QPushButton:disabled {
                background-color: #B3D7FF;
            }
        """)
        self.send_btn.clicked.connect(self.send_message)
        
        input_layout.addWidget(self.attach_btn)
        input_layout.addWidget(self.mic_btn)
        input_layout.addWidget(self.input_field, 1)
        input_layout.addWidget(self.send_btn)

        self.attached_label = QLabel("")
        self.attached_label.setFont(QFont("-apple-system", 12))
        self.attached_label.setStyleSheet("color: #8E8E93;")
        layout.addWidget(self.attached_label)

        layout.addWidget(input_container)
    
    def _attach_audio(self):
        """Open file dialog for audio/voice message."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Audio File",
            "",
            "Audio (*.mp3 *.wav *.m4a *.ogg *.oga *.webm);;All files (*)",
        )
        if path:
            self.pending_audio = path
            self._update_attached_label()

    def _attach_file(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Attach image or audio",
            "",
            "Images (*.png *.jpg *.jpeg *.gif *.webp);;"
            "Audio (*.mp3 *.wav *.m4a *.ogg *.oga *.webm);;"
            "All files (*)",
        )
        self._add_attachments(paths)

    def _add_attachments(self, paths: list[str]):
        _AUDIO_EXT = (".mp3", ".wav", ".m4a", ".ogg", ".oga", ".webm")
        _IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
        for p in paths:
            if not p:
                continue
            low = p.lower()
            if any(low.endswith(ext) for ext in _AUDIO_EXT):
                self.pending_audio = p
            elif any(low.endswith(ext) for ext in _IMAGE_EXT) and p not in self.pending_images:
                self.pending_images.append(p)
                self.image_attached.emit(p)
        self._update_attached_label()

    def _input_drag_enter(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _input_drop(self, event: QDropEvent):
        _AUDIO_EXT = (".mp3", ".wav", ".m4a", ".ogg", ".oga", ".webm")
        _IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        for p in paths:
            if not p:
                continue
            low = p.lower()
            if any(low.endswith(ext) for ext in _AUDIO_EXT):
                self.pending_audio = p
            elif any(low.endswith(ext) for ext in _IMAGE_EXT) and p not in self.pending_images:
                self.pending_images.append(p)
                self.image_attached.emit(p)
        self._update_attached_label()

    def _update_attached_label(self):
        parts = []
        if self.pending_images:
            parts.append(f"📷 {len(self.pending_images)} image(s)")
        if self.pending_audio:
            parts.append("🎤 1 audio")
        if not parts:
            self.attached_label.setText("")
            self.attached_label.hide()
        else:
            self.attached_label.setText(" • ".join(parts) + " attached")
            self.attached_label.show()

    def _new_chat(self):
        """Clear conversation and start fresh."""
        import asyncio
        asyncio.run(self.agent.clear_session(self.user_id))
        # Remove all message bubbles, keep empty state and stretch
        for i in range(self.chat_layout.count() - 1, -1, -1):
            item = self.chat_layout.itemAt(i)
            if not item:
                continue
            w = item.widget()
            if w and isinstance(w, MessageBubble):
                w.deleteLater()
        self.empty_state.show()
        mw = self.window()
        if mw and hasattr(mw, "status_bar"):
            mw.status_bar.showMessage("New conversation started")

    def _set_loading(self, loading: bool):
        """Enable/disable send during streaming, show loading state."""
        self.send_btn.setEnabled(not loading)
        self.input_field.setEnabled(not loading)
        self.attach_btn.setEnabled(not loading)
        self.mic_btn.setEnabled(not loading)
        if loading:
            self.send_btn.setText("...")
        else:
            self.send_btn.setText("Send")

    def send_message(self):
        text = self.input_field.text().strip()
        images = list(self.pending_images)
        audio_path = self.pending_audio
        if not text and not images and not audio_path:
            return

        self._set_loading(True)
        self.input_field.clear()
        self.pending_images.clear()
        self.pending_audio = None
        self._update_attached_label()

        if text:
            display_text = text
        elif audio_path:
            display_text = "(audio message)"
        else:
            display_text = "(image)"

        self.add_message(display_text, is_user=True)

        self._streaming_bubble = None
        self._user_near_bottom = True
        prompt = text or ("What's in this image?" if images else "")
        self.worker = MessageWorker(
            self.agent, self.user_id, prompt, images=images, audio_path=audio_path
        )
        self.worker.chunk_ready.connect(self._on_stream_chunk)
        self.worker.message_ready.connect(self.on_message_ready)
        self.worker.error_occurred.connect(self.on_error)
        self.worker.start()

    def _scroll_to_bottom_if_near(self):
        """Scroll to bottom only if user is near the bottom (within 80px)."""
        if not getattr(self, "_user_near_bottom", True):
            return
        sb = self.chat_scroll.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_scroll_changed(self):
        """Track if user is near bottom for smart scroll."""
        sb = self.chat_scroll.verticalScrollBar()
        threshold = 80
        self._user_near_bottom = sb.value() >= sb.maximum() - threshold

    def _on_speak_requested(self, text: str):
        """Handle speak button - run TTS in background."""
        if not text or not text.strip():
            return
        worker = TTSWorker(text)
        worker.start()

    def _on_stream_chunk(self, chunk: str):
        """Append a streamed chunk to the assistant bubble."""
        if self._streaming_bubble is None:
            self._streaming_bubble = MessageBubble("", is_user=False, is_dark=self.is_dark)
            self._streaming_bubble.speak_requested.connect(self._on_speak_requested)
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, self._streaming_bubble)
        current = self._streaming_bubble.label.text()
        self._streaming_bubble.label.setText(current + chunk)
        QTimer.singleShot(0, self._scroll_to_bottom_if_near)

    def on_message_ready(self, response_text):
        """Handle completion of the response from the worker thread."""
        self._set_loading(False)
        if not (response_text or "").strip():
            response_text = "I couldn't generate a response. Please check that your LLM provider (Ollama, LM Studio, etc.) is running and try again."
        if self._streaming_bubble is not None:
            self._streaming_bubble.label.setText(response_text)
            self._streaming_bubble = None
        else:
            self.message_received.emit(response_text, False)
    
    def on_error(self, error_message):
        """Handle errors from the worker thread."""
        self._set_loading(False)
        if self._streaming_bubble is not None:
            self._streaming_bubble.label.setText(error_message)
            self._streaming_bubble = None
        else:
            self.message_received.emit(error_message, False)
    
    def _export_conversation(self):
        """Export current conversation to a file (Markdown or plain text)."""
        messages = []
        for i in range(self.chat_layout.count()):
            item = self.chat_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), MessageBubble):
                bubble = item.widget()
                text = bubble.label.text().strip()
                if text:
                    messages.append((bubble.is_user, text))

        if not messages:
            QMessageBox.information(
                self,
                "Export Conversation",
                "No messages to export.",
            )
            return

        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Conversation",
            "",
            "Markdown (*.md);;Plain Text (*.txt);;All files (*)",
        )
        if not path:
            return

        if selected_filter.startswith("Markdown") and not path.lower().endswith(".md"):
            path = path + ".md"
        elif selected_filter.startswith("Plain") and not path.lower().endswith(".txt"):
            path = path + ".txt"

        try:
            if path.lower().endswith(".md"):
                lines = ["# GrizzyClaw Conversation\n"]
                for is_user, text in messages:
                    role = "You" if is_user else "Assistant"
                    lines.append(f"## {role}\n\n{text}\n\n")
                content = "".join(lines)
            else:
                lines = []
                for is_user, text in messages:
                    role = "You" if is_user else "Assistant"
                    lines.append(f"{role}: {text}\n")
                content = "".join(lines)

            Path(path).write_text(content, encoding="utf-8")
            QMessageBox.information(
                self,
                "Export Conversation",
                f"Conversation exported to:\n{path}",
            )
        except Exception as e:
            QMessageBox.warning(
                self,
                "Export Failed",
                f"Could not export: {e}",
            )

    def add_message(self, text, is_user=True):
        self.empty_state.hide()
        bubble = MessageBubble(text, is_user, is_dark=self.is_dark)
        if not is_user:
            bubble.speak_requested.connect(self._on_speak_requested)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)

        # Scroll to bottom (smart scroll: only when user is near bottom)
        if getattr(self, "_user_near_bottom", True):
            QTimer.singleShot(50, self._scroll_to_bottom_if_near)
    
    def update_workspace_name(self, name: str, icon: str = "🤖"):
        """Update chat header to show workspace name."""
        if name:
            self.header_label.setText(f"Chat - {icon} {name}")
        else:
            self.header_label.setText("Chat")

    def update_separator_colors(self, border_color: str):
        """Update separator styling to use theme border color."""
        style = f"background-color: {border_color}; max-height: 1px;"
        self.separator_top.setStyleSheet(style)
        self.separator_bottom.setStyleSheet(style)

    def update_theme(self, is_dark, border_color: str = None):
        """Update theme for all existing message bubbles"""
        self.is_dark = is_dark
        if border_color:
            self.update_separator_colors(border_color)
        # Update input field style
        if is_dark:
            self.input_field.setStyleSheet("""
                QLineEdit {
                    padding: 0 16px;
                    border: 1px solid #48484A;
                    border-radius: 22px;
                    background: #3A3A3C;
                    color: #FFFFFF;
                }
                QLineEdit:focus {
                    border-color: #0A84FF;
                    border-width: 2px;
                }
                QLineEdit::placeholder {
                    color: #8E8E93;
                }
            """)
        else:
            self.input_field.setStyleSheet("""
                QLineEdit {
                    padding: 0 16px;
                    border: 1px solid #D1D1D6;
                    border-radius: 22px;
                    background: #FFFFFF;
                    color: #1C1C1E;
                }
                QLineEdit:focus {
                    border-color: #007AFF;
                    border-width: 2px;
                }
                QLineEdit::placeholder {
                    color: #8E8E93;
                }
            """)
        
        # Update all existing message bubbles
        for i in range(self.chat_layout.count()):
            widget = self.chat_layout.itemAt(i).widget()
            if isinstance(widget, MessageBubble):
                widget.update_style(is_dark)


class SidebarWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.workspace_manager = None
        self.on_switch_workspace = None
        self.workspace_buttons: list = []
        self._theme_colors = None
        self.setup_ui()

    def set_theme_colors(self, theme_colors: dict):
        """Store theme colors for use when refreshing workspace buttons."""
        self._theme_colors = theme_colors
        fg = theme_colors.get("fg", "#1C1C1E")
        accent = theme_colors.get("accent", "#007AFF")
        if hasattr(self, "logo_text"):
            self.logo_text.setStyleSheet(f"color: {fg};")
        if hasattr(self, "logo_text2"):
            self.logo_text2.setStyleSheet(f"color: {accent};")
        if hasattr(self, "sep_settings"):
            self.sep_settings.setStyleSheet(f"background-color: {theme_colors.get('border', '#E5E5EA')}; max-height: 1px;")
        self.refresh_workspace_buttons()

    def set_workspace_manager(self, manager, switch_callback):
        """Set workspace manager and callback for switching. Call refresh_workspace_buttons after."""
        self.workspace_manager = manager
        self.on_switch_workspace = switch_callback

    def refresh_workspace_buttons(self):
        """Rebuild workspace switch buttons from current workspace list."""
        for btn in self.workspace_buttons:
            btn.deleteLater()
        self.workspace_buttons.clear()

        if not self.workspace_manager or not self.on_switch_workspace:
            return

        workspaces = self.workspace_manager.list_workspaces()
        active_id = self.workspace_manager.active_workspace_id

        fg = self._theme_colors.get("fg", "#1C1C1E") if self._theme_colors else "#1C1C1E"
        accent = self._theme_colors.get("accent", "#007AFF") if self._theme_colors else "#007AFF"
        hover = "rgba(255, 255, 255, 0.1)" if self._theme_colors and self._theme_colors.get("is_dark") else "rgba(0, 0, 0, 0.05)"
        for ws in workspaces:
            btn = QPushButton(f"{ws.icon}  {ws.name}")
            btn.setProperty("workspace_id", ws.id)
            btn.setFont(QFont("-apple-system", 13))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(36)
            is_active = ws.id == active_id
            btn.setStyleSheet(f"""
                QPushButton {{
                    padding: 0 16px;
                    background-color: transparent;
                    color: {fg};
                    border: none;
                    border-radius: 8px;
                    text-align: left;
                }}
                QPushButton:hover {{
                    background-color: {hover};
                }}
            """ if not is_active else f"""
                QPushButton {{
                    padding: 0 16px;
                    background-color: {accent};
                    color: white;
                    border: none;
                    border-radius: 8px;
                    text-align: left;
                }}
            """)
            btn.clicked.connect(lambda checked=False, wid=ws.id: self._on_workspace_click(wid))
            self.workspace_buttons_layout.addWidget(btn)
            self.workspace_buttons.append(btn)

    def _on_workspace_click(self, workspace_id: str):
        if self.on_switch_workspace:
            self.on_switch_workspace(workspace_id)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 20, 16, 20)
        layout.setSpacing(0)
        
        # Logo with proper spacing
        logo_container = QWidget()
        logo_layout = QHBoxLayout(logo_container)
        logo_layout.setContentsMargins(12, 0, 0, 0)
        
        logo_icon = QLabel("🐻")
        logo_icon.setFont(QFont("-apple-system", 28))
        logo_layout.addWidget(logo_icon)
        
        self.logo_text = QLabel("Grizzy")
        self.logo_text.setFont(QFont("-apple-system", 20, QFont.Weight.Bold))
        self.logo_text.setStyleSheet("color: #1C1C1E;")
        logo_layout.addWidget(self.logo_text)
        
        self.logo_text2 = QLabel("Claw")
        self.logo_text2.setFont(QFont("-apple-system", 20, QFont.Weight.Bold))
        self.logo_text2.setStyleSheet("color: #007AFF;")
        logo_layout.addWidget(self.logo_text2)
        
        logo_layout.addStretch()
        layout.addWidget(logo_container)
        
        layout.addSpacing(30)
        
        # Section label
        nav_label = QLabel("MENU")
        nav_label.setFont(QFont("-apple-system", 11, QFont.Weight.Medium))
        nav_label.setStyleSheet("color: #8E8E93;")
        nav_label.setContentsMargins(12, 0, 0, 0)
        layout.addWidget(nav_label)
        
        layout.addSpacing(8)
        
        # Navigation buttons with better spacing
        self.chat_btn = self.create_nav_button("💬", "Chat", True)
        self.workspaces_btn = self.create_nav_button("🗂️", "Workspaces")
        self.memory_btn = self.create_nav_button("🧠", "Memory")
        self.scheduler_btn = self.create_nav_button("⏰", "Scheduler")
        self.browser_btn = self.create_nav_button("🌐", "Browser")
        self.settings_btn = self.create_nav_button("⚙️", "Settings")
        
        layout.addWidget(self.chat_btn)
        layout.addSpacing(4)
        layout.addWidget(self.workspaces_btn)
        layout.addSpacing(4)
        layout.addWidget(self.memory_btn)
        layout.addSpacing(4)
        layout.addWidget(self.scheduler_btn)
        layout.addSpacing(4)
        layout.addWidget(self.browser_btn)
        layout.addSpacing(4)
        layout.addWidget(self.settings_btn)

        self.sep_settings = QFrame()
        self.sep_settings.setFrameShape(QFrame.Shape.HLine)
        self.sep_settings.setStyleSheet("background-color: #E5E5EA; max-height: 1px;")
        self.sep_settings.setFixedHeight(1)
        layout.addWidget(self.sep_settings)

        layout.addSpacing(20)

        sep_workspaces = QFrame()
        sep_workspaces.setFrameShape(QFrame.Shape.HLine)
        sep_workspaces.setStyleSheet("background-color: #E5E5EA; max-height: 1px;")
        sep_workspaces.setFixedHeight(1)
        layout.addWidget(sep_workspaces)

        layout.addSpacing(8)

        # Workspace switch buttons
        workspace_section = QWidget()
        self.workspace_buttons_layout = QVBoxLayout(workspace_section)
        self.workspace_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.workspace_buttons_layout.setSpacing(4)
        layout.addWidget(workspace_section)

        layout.addStretch()

        # Status at bottom with separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("background-color: #E5E5EA; max-height: 1px;")
        separator.setFixedHeight(1)
        layout.addWidget(separator)
        
        layout.addSpacing(12)
        
        status_container = QWidget()
        status_layout = QHBoxLayout(status_container)
        status_layout.setContentsMargins(12, 0, 0, 0)
        status_layout.setSpacing(8)
        
        status_dot = QLabel("●")
        status_dot.setFont(QFont("-apple-system", 10))
        status_dot.setStyleSheet("color: #34C759;")
        status_layout.addWidget(status_dot)
        
        self.status_label = QLabel("Connected")
        self.status_label.setFont(QFont("-apple-system", 13))
        self.status_label.setStyleSheet("color: #34C759;")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()
        
        layout.addWidget(status_container)
        
        self.setFixedWidth(240)
        self.setStyleSheet("""
            SidebarWidget {
                background-color: #F5F5F7;
                border-right: 1px solid #E5E5EA;
            }
        """)
    
    def create_nav_button(self, icon, text, active=False):
        btn = QPushButton(f"{icon}  {text}")
        btn.setFont(QFont("-apple-system", 14))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(40)
        
        if active:
            btn.setStyleSheet("""
                QPushButton {
                    padding: 0 16px;
                    background-color: #007AFF;
                    color: white;
                    border: none;
                    border-radius: 10px;
                    text-align: left;
                }
            """)
        else:
            btn.setStyleSheet("""
                QPushButton {
                    padding: 0 16px;
                    background-color: transparent;
                    color: #3C3C43;
                    border: none;
                    border-radius: 10px;
                    text-align: left;
                }
                QPushButton:hover {
                    background-color: rgba(0, 0, 0, 0.05);
                }
            """)
        
        return btn


class GrizzyClawApp(QMainWindow):
    def __init__(self):
        super().__init__()
        # Load settings from config file if it exists (same path used for save)
        self.settings = Settings()
        config_path = get_config_path()
        if config_path.exists():
            try:
                self.settings = Settings.from_file(str(config_path))
            except Exception:
                pass
        
        # Initialize workspace manager
        self.workspace_manager = WorkspaceManager()
        
        # Create agent from active workspace (or default)
        active_ws = self.workspace_manager.get_active_workspace()
        if active_ws:
            self.agent = self.workspace_manager.create_agent_for_workspace(
                active_ws.id, self.settings
            )
        else:
            self.agent = AgentCore(self.settings)
        
        self.telegram_bot = None
        self._stop_worker = None
        self.user_id = "gui_user"
        
        self.setWindowTitle("GrizzyClaw")
        self.setMinimumSize(1100, 700)
        self.resize(1300, 850)
        
        self.setup_ui()
        self.setup_menu()
        self.setup_tray()
        self.setup_shortcuts()

        # Clean up workers before quit to avoid macOS "quit unexpectedly" crash
        app = QApplication.instance()
        if app:
            app.aboutToQuit.connect(self._cleanup_before_quit)
        
        # Apply appearance settings on startup
        self.apply_appearance_settings()

        # Set initial workspace name in chat header
        active_ws = self.workspace_manager.get_active_workspace()
        if active_ws:
            self.chat_widget.update_workspace_name(active_ws.name, active_ws.icon)

        if self.settings.telegram_bot_token:
            self.start_telegram()
    
    def setup_ui(self):
        # Set main window background
        self.setStyleSheet("""
            QMainWindow {
                background-color: #FFFFFF;
            }
        """)
        
        central = QWidget()
        self.setCentralWidget(central)
        
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Sidebar
        self.sidebar = SidebarWidget()
        self.sidebar.set_workspace_manager(
            self.workspace_manager,
            self.on_workspace_changed,
        )
        self.sidebar.refresh_workspace_buttons()
        self.sidebar.chat_btn.clicked.connect(self.show_chat)
        self.sidebar.workspaces_btn.clicked.connect(self.show_workspaces)
        self.sidebar.memory_btn.clicked.connect(self.show_memory)
        self.sidebar.scheduler_btn.clicked.connect(self.show_scheduler)
        self.sidebar.browser_btn.clicked.connect(self.show_browser)
        self.sidebar.settings_btn.clicked.connect(self.show_settings)
        layout.addWidget(self.sidebar)
        
        # Main content: chat + visual canvas splitter
        self.content_stack = QWidget()
        self.content_stack.setStyleSheet("background-color: #FFFFFF;")
        self.content_layout = QVBoxLayout(self.content_stack)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.chat_widget = ChatWidget(self.agent)
        self.canvas_widget = CanvasWidget()
        self.main_splitter.addWidget(self.chat_widget)
        self.main_splitter.addWidget(self.canvas_widget)
        self.main_splitter.setSizes([700, 300])  # Chat gets more space by default
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 0)
        self.chat_widget.image_attached.connect(self.canvas_widget.display_image)
        self.content_layout.addWidget(self.main_splitter)

        layout.addWidget(self.content_stack, 1)
        
        # Status bar with better styling
        self.status_bar = QStatusBar()
        self.status_bar.setStyleSheet("""
            QStatusBar {
                background-color: #F5F5F7;
                color: #3C3C43;
                border-top: 1px solid #E5E5EA;
                padding: 4px 16px;
            }
        """)
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
    
    def setup_menu(self):
        menubar = self.menuBar()
        menubar.setStyleSheet("""
            QMenuBar {
                background-color: #F5F5F7;
                border-bottom: 1px solid #E5E5EA;
            }
            QMenuBar::item {
                padding: 6px 16px;
                background: transparent;
            }
            QMenuBar::item:selected {
                background-color: #007AFF;
                color: white;
                border-radius: 4px;
            }
        """)
        
        # File menu
        file_menu = menubar.addMenu("File")
        
        settings_action = QAction("Preferences...", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self.show_settings)
        file_menu.addAction(settings_action)

        new_chat_action = QAction("New Chat", self)
        new_chat_action.setShortcut("Ctrl+N")
        new_chat_action.triggered.connect(self.chat_widget._new_chat)
        file_menu.addAction(new_chat_action)

        export_action = QAction("Export Conversation...", self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(lambda: self.chat_widget._export_conversation())
        file_menu.addAction(export_action)

        file_menu.addSeparator()

        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.quit_app)
        file_menu.addAction(quit_action)
        
        # View menu
        view_menu = menubar.addMenu("View")
        
        toggle_tray_action = QAction("Hide to Tray", self)
        toggle_tray_action.triggered.connect(self.hide_to_tray)
        view_menu.addAction(toggle_tray_action)
        
        # Help menu
        help_menu = menubar.addMenu("Help")
        
        about_action = QAction("About GrizzyClaw", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
    
    def setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setToolTip("GrizzyClaw")
        import os
        meipass = getattr(sys, '_MEIPASS', '.')
        icon_path = os.path.join(meipass, 'img.png')
        self.tray_icon.setIcon(QIcon(icon_path))
        
        # Create tray menu
        tray_menu = QMenu()
        
        show_action = QAction("Show", self)
        show_action.triggered.connect(self.show_normal)
        tray_menu.addAction(show_action)
        
        tray_menu.addSeparator()
        
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_app)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.tray_activated)
        self.tray_icon.show()
    
    def setup_shortcuts(self):
        settings_shortcut = QShortcut(QKeySequence("Ctrl+,"), self)
        settings_shortcut.activated.connect(self.show_settings)

        new_chat_shortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        new_chat_shortcut.activated.connect(self.chat_widget._new_chat)
        
        close_shortcut = QShortcut(QKeySequence("Esc"), self)
        close_shortcut.activated.connect(self.hide_to_tray)
    
    def start_telegram(self):
        try:
            token = _sanitize_telegram_token(self.settings.telegram_bot_token or "")
            if not token:
                self.status_bar.showMessage("Telegram: No valid token configured")
                return
            self.settings.telegram_bot_token = token
            self.telegram_bot = TelegramChannel(self.settings)
            self._telegram_worker = TelegramStartWorker(self.telegram_bot, self)
            self._telegram_worker.failed.connect(self._on_telegram_start_failed)
            self._telegram_worker.start()
            self.status_bar.showMessage("Telegram bot starting...")
            QTimer.singleShot(3000, self._check_telegram_started)
        except Exception as e:
            self.status_bar.showMessage(f"Telegram error: {e}")

    def _on_telegram_start_failed(self, err: str):
        """Called when Telegram bot fails to start (e.g. network, auth)."""
        self.telegram_bot = None
        self.status_bar.showMessage(f"Telegram failed: {err}")
        QMessageBox.warning(
            self,
            "Telegram Connection Failed",
            f"The Telegram bot could not start:\n\n{err}\n\n"
            "Check that your token is correct and you have internet access.",
        )

    def _check_telegram_started(self):
        """If bot thread is still running after 3s, assume it started successfully."""
        if self.telegram_bot and self._telegram_worker.isRunning():
            self.status_bar.showMessage("Telegram connected – send /start to your bot")

    def _stop_telegram_then_start(self):
        """Stop existing Telegram bot, then start with current token. Used when token changes."""
        old_bot = self.telegram_bot
        self.telegram_bot = None
        if not old_bot:
            self.start_telegram()
            return

        class StopWorker(QThread):
            done = pyqtSignal()

            def run(self):
                try:
                    asyncio.run(old_bot.stop())
                except Exception:
                    pass
                self.done.emit()

        w = StopWorker(self)
        w.done.connect(lambda: self._on_telegram_stopped())
        w.start()
        self._stop_worker = w  # keep ref so it's not garbage-collected

    def _on_telegram_stopped(self):
        """Called after Telegram bot has stopped; start with new token."""
        self._stop_worker = None
        if self.settings.telegram_bot_token:
            self.start_telegram()
            self.status_bar.showMessage("Telegram reconnected with new token")
    
    def show_chat(self):
        self.chat_widget.show()
    
    def show_memory(self):
        dialog = MemoryDialog(self.agent, self.chat_widget.user_id, self)
        dialog.exec()
    
    def show_scheduler(self):
        dialog = SchedulerDialog(self.agent, self)
        dialog.exec()
    
    def show_browser(self):
        dialog = BrowserDialog(self.agent, self)
        dialog.exec()
    
    def show_workspaces(self):
        dialog = WorkspaceDialog(self.workspace_manager, self)
        dialog.workspace_changed.connect(self.on_workspace_changed)
        dialog.workspace_config_saved.connect(self.on_workspace_config_saved)
        dialog.exec()
        self.sidebar.refresh_workspace_buttons()
    
    def on_workspace_changed(self, workspace_id: str):
        """Handle workspace switch (from sidebar button or workspace dialog)."""
        self.workspace_manager.set_active_workspace(workspace_id)
        new_agent = self.workspace_manager.create_agent_for_workspace(
            workspace_id, self.settings
        )
        if new_agent:
            self.agent = new_agent
            # Update chat widget with new agent
            self.chat_widget.agent = new_agent
            # Update window title and chat header with workspace name
            workspace = self.workspace_manager.get_workspace(workspace_id)
            if workspace:
                self.setWindowTitle(f"GrizzyClaw - {workspace.icon} {workspace.name}")
                self.chat_widget.update_workspace_name(workspace.name, workspace.icon)
            self.status_bar.showMessage(f"Switched to workspace: {workspace.name if workspace else workspace_id}")
            self.sidebar.refresh_workspace_buttons()

    def on_workspace_config_saved(self, workspace_id: str):
        """When a workspace's config is saved, recreate agent if it's the active one so chat uses new provider/model."""
        active_ws = self.workspace_manager.get_active_workspace()
        if not active_ws or active_ws.id != workspace_id:
            return
        new_agent = self.workspace_manager.create_agent_for_workspace(workspace_id, self.settings)
        if new_agent:
            self.agent = new_agent
            self.chat_widget.agent = new_agent
            self.status_bar.showMessage("Workspace saved. Chat now uses this workspace's provider and model.")
    
    def show_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.settings = dialog.get_settings()
            self.apply_appearance_settings()
            # Recreate agent so it uses updated LLM URLs (e.g. remote LM Studio at 192.168.x.x)
            active_ws = self.workspace_manager.get_active_workspace()
            if active_ws:
                new_agent = self.workspace_manager.create_agent_for_workspace(
                    active_ws.id, self.settings
                )
            else:
                new_agent = AgentCore(self.settings)
            if new_agent:
                self.agent = new_agent
                self.chat_widget.agent = new_agent
            # Start or restart Telegram when token is present
            if self.settings.telegram_bot_token:
                if self.telegram_bot is None:
                    self.start_telegram()
                    self.status_bar.showMessage("Settings saved. Telegram connected.")
                else:
                    self._stop_telegram_then_start()
                    self.status_bar.showMessage("Settings saved. Reconnecting Telegram...")
            else:
                self.status_bar.showMessage("Settings saved")

    def _resolve_theme(self, theme: str) -> str:
        """Resolve 'Auto (System)' to Light or Dark based on system preference."""
        if theme != "Auto (System)":
            return theme
        try:
            app = QApplication.instance()
            if app and hasattr(app, "styleHints"):
                hints = app.styleHints()
                if hasattr(hints, "colorScheme"):
                    scheme = hints.colorScheme()
                    if scheme == Qt.ColorScheme.Dark:
                        return "Dark"
                    if scheme == Qt.ColorScheme.Light:
                        return "Light"
        except Exception:
            pass
        return "Light"

    def get_theme_colors(self, theme):
        """Get color scheme for the selected theme"""
        themes = {
            "Light": {
                'is_dark': False,
                'bg': '#FFFFFF',
                'fg': '#1C1C1E',
                'sidebar_bg': '#F5F5F7',
                'border': '#E5E5EA',
                'input_bg': '#FFFFFF',
                'input_border': '#D1D1D6',
                'accent': '#007AFF',
                'secondary': '#8E8E93'
            },
            "Dark": {
                'is_dark': True,
                'bg': '#1E1E1E',
                'fg': '#FFFFFF',
                'sidebar_bg': '#2D2D2D',
                'border': '#3A3A3C',
                'input_bg': '#3A3A3C',
                'input_border': '#48484A',
                'accent': '#0A84FF',
                'secondary': '#8E8E93'
            },
            "High Contrast Light": {
                'is_dark': False,
                'bg': '#FFFFFF',
                'fg': '#000000',
                'sidebar_bg': '#F0F0F0',
                'border': '#000000',
                'input_bg': '#FFFFFF',
                'input_border': '#000000',
                'accent': '#0000FF',
                'secondary': '#666666'
            },
            "High Contrast Dark": {
                'is_dark': True,
                'bg': '#000000',
                'fg': '#FFFFFF',
                'sidebar_bg': '#1A1A1A',
                'border': '#FFFFFF',
                'input_bg': '#1A1A1A',
                'input_border': '#FFFFFF',
                'accent': '#00D9FF',
                'secondary': '#AAAAAA'
            },
            "Nord": {
                'is_dark': True,
                'bg': '#2E3440',
                'fg': '#ECEFF4',
                'sidebar_bg': '#3B4252',
                'border': '#4C566A',
                'input_bg': '#3B4252',
                'input_border': '#4C566A',
                'accent': '#88C0D0',
                'secondary': '#D8DEE9'
            },
            "Solarized Light": {
                'is_dark': False,
                'bg': '#FDF6E3',
                'fg': '#657B83',
                'sidebar_bg': '#EEE8D5',
                'border': '#93A1A1',
                'input_bg': '#EEE8D5',
                'input_border': '#93A1A1',
                'accent': '#268BD2',
                'secondary': '#93A1A1'
            },
            "Solarized Dark": {
                'is_dark': True,
                'bg': '#002B36',
                'fg': '#839496',
                'sidebar_bg': '#073642',
                'border': '#586E75',
                'input_bg': '#073642',
                'input_border': '#586E75',
                'accent': '#268BD2',
                'secondary': '#93A1A1'
            },
            "Dracula": {
                'is_dark': True,
                'bg': '#282A36',
                'fg': '#F8F8F2',
                'sidebar_bg': '#21222C',
                'border': '#44475A',
                'input_bg': '#44475A',
                'input_border': '#6272A4',
                'accent': '#BD93F9',
                'secondary': '#6272A4'
            },
            "Monokai": {
                'is_dark': True,
                'bg': '#272822',
                'fg': '#F8F8F2',
                'sidebar_bg': '#1E1F1C',
                'border': '#3E3D32',
                'input_bg': '#3E3D32',
                'input_border': '#49483E',
                'accent': '#F92672',
                'secondary': '#75715E'
            }
        }

        # Default to Light theme if unknown
        return themes.get(theme, themes["Light"])
    
    def apply_appearance_settings(self):
        # Apply font
        font_family = self.settings.font_family
        if font_family == "System Default":
            font_family = "-apple-system"
        font_size = self.settings.font_size

        font = QFont(font_family, font_size)
        QApplication.instance().setFont(font)

        # Apply theme (resolve Auto to system preference)
        theme = self._resolve_theme(self.settings.theme)

        # Define theme colors
        theme_colors = self.get_theme_colors(theme)
        is_dark = theme_colors['is_dark']

        if is_dark:
            # Apply dark theme with custom colors
            self.setStyleSheet(f"""
                /* Main Window */
                QMainWindow {{
                    background-color: {theme_colors['bg']};
                }}

                /* Central Widget */
                QWidget {{
                    background-color: {theme_colors['bg']};
                    color: {theme_colors['fg']};
                }}

                /* Sidebar */
                SidebarWidget {{
                    background-color: {theme_colors['sidebar_bg']};
                    border-right: 1px solid {theme_colors['border']};
                }}

                /* Labels */
                QLabel {{
                    color: {theme_colors['fg']};
                    background: transparent;
                }}

                /* Chat Header */
                QLabel[text="Chat"] {{
                    color: {theme_colors['fg']};
                    font-weight: bold;
                }}

                /* Separator Lines */
                QFrame {{
                    background-color: {theme_colors['border']};
                    border: none;
                }}

                /* Input Field */
                QLineEdit {{
                    background-color: {theme_colors['input_bg']};
                    color: {theme_colors['fg']};
                    border: 1px solid {theme_colors['input_border']};
                    border-radius: 22px;
                    padding: 0 16px;
                    selection-background-color: {theme_colors['accent']};
                }}
                QLineEdit:focus {{
                    border: 2px solid {theme_colors['accent']};
                }}
                QLineEdit::placeholder {{
                    color: {theme_colors['secondary']};
                }}

                /* Send Button */
                QPushButton {{
                    background-color: {theme_colors['accent']};
                    color: white;
                    border: none;
                    border-radius: 22px;
                    font-weight: 500;
                }}
                QPushButton:hover {{
                    background-color: {theme_colors['accent']};
                    opacity: 0.8;
                }}
                QPushButton:pressed {{
                    background-color: {theme_colors['accent']};
                    opacity: 0.6;
                }}

                /* Navigation Buttons */
                QPushButton {{
                    background-color: transparent;
                    color: {theme_colors['fg']};
                    border: none;
                    border-radius: 10px;
                    text-align: left;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.1);
                }}
                QPushButton:checked, QPushButton[active="true"] {{
                    background-color: {theme_colors['accent']};
                    color: white;
                }}

                /* Status Bar */
                QStatusBar {{
                    background-color: {theme_colors['sidebar_bg']};
                    color: {theme_colors['secondary']};
                    border-top: 1px solid {theme_colors['border']};
                }}

                /* Menu Bar */
                QMenuBar {{
                    background-color: {theme_colors['sidebar_bg']};
                    border-bottom: 1px solid {theme_colors['border']};
                    color: {theme_colors['fg']};
                }}
                QMenuBar::item {{
                    background: transparent;
                    color: {theme_colors['fg']};
                }}
                QMenuBar::item:selected {{
                    background-color: {theme_colors['accent']};
                    color: white;
                }}

                /* Scroll Bars */
                QScrollBar:vertical {{
                    background: transparent;
                    width: 8px;
                    margin: 0px;
                }}
                QScrollBar::handle:vertical {{
                    background: {theme_colors['input_border']};
                    border-radius: 4px;
                    min-height: 30px;
                }}
                QScrollBar::handle:vertical:hover {{
                    background: {theme_colors['secondary']};
                }}

                /* Message Bubbles */
                QLabel[message="user"] {{
                    background-color: {theme_colors['accent']};
                    color: white;
                }}
                QLabel[message="assistant"] {{
                    background-color: {theme_colors['input_bg']};
                    color: {theme_colors['fg']};
                }}
            """)


            # Update sidebar style specifically
            self.sidebar.setStyleSheet(f"""
                SidebarWidget {{
                    background-color: {theme_colors['sidebar_bg']};
                    border-right: 1px solid {theme_colors['border']};
                }}
                QLabel {{
                    color: {theme_colors['fg']};
                    background: transparent;
                }}
            """)

            # Update status bar
            self.status_bar.setStyleSheet(f"""
                QStatusBar {{
                    background-color: {theme_colors['sidebar_bg']};
                    color: {theme_colors['secondary']};
                    border-top: 1px solid {theme_colors['border']};
                }}
            """)

            # Update content area
            self.content_stack.setStyleSheet(f"background-color: {theme_colors['bg']};")

            # Update chat widget theme
            self.chat_widget.update_theme(True, theme_colors["border"])
            self.chat_widget.update_separator_colors(theme_colors["border"])
            # Update sidebar theme
            self.sidebar.set_theme_colors(theme_colors)


        else:  # Light themes
            self.setStyleSheet(f"""
                QMainWindow {{
                    background-color: {theme_colors['bg']};
                }}
                QWidget {{
                    background-color: {theme_colors['bg']};
                    color: {theme_colors['fg']};
                }}
                QLabel {{
                    color: {theme_colors['fg']};
                    background: transparent;
                }}
                /* Input Field */
                QLineEdit {{
                    background-color: {theme_colors['input_bg']};
                    color: {theme_colors['fg']};
                    border: 1px solid {theme_colors['input_border']};
                    border-radius: 22px;
                    padding: 0 16px;
                }}
                QLineEdit:focus {{
                    border: 2px solid {theme_colors['accent']};
                }}
                /* Send Button */
                QPushButton {{
                    background-color: {theme_colors['accent']};
                    color: white;
                    border: none;
                    border-radius: 22px;
                }}
                /* Separator Lines */
                QFrame {{
                    background-color: {theme_colors['border']};
                    border: none;
                }}
            """)

            # Update sidebar
            self.sidebar.setStyleSheet(f"""
                SidebarWidget {{
                    background-color: {theme_colors['sidebar_bg']};
                    border-right: 1px solid {theme_colors['border']};
                }}
                QLabel {{
                    color: {theme_colors['fg']};
                    background: transparent;
                }}
            """)

            # Update status bar
            self.status_bar.setStyleSheet(f"""
                QStatusBar {{
                    background-color: {theme_colors['sidebar_bg']};
                    color: {theme_colors['fg']};
                    border-top: 1px solid {theme_colors['border']};
                }}
            """)

            # Update content area
            self.content_stack.setStyleSheet(f"background-color: {theme_colors['bg']};")

            # Update chat widget theme
            self.chat_widget.update_theme(False, theme_colors["border"])
            self.chat_widget.update_separator_colors(theme_colors["border"])
            # Update sidebar theme
            self.sidebar.set_theme_colors(theme_colors)
    
    def show_about(self):
        QMessageBox.about(
            self,
            "About GrizzyClaw",
            "<h2>GrizzyClaw v0.1.0</h2>"
            "<p>A secure, multi-platform AI agent with local LLM support.</p>"
            "<p>Features:</p>"
            "<ul>"
            "<li>Local LLMs (Ollama, LM Studio)</li>"
            "<li>Cloud LLMs (OpenAI, Anthropic)</li>"
            "<li>Telegram integration</li>"
            "<li>Persistent memory</li>"
            "</ul>",
        )
    
    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_normal()
    
    def show_normal(self):
        self.show()
        self.raise_()
        self.activateWindow()
    
    def hide_to_tray(self):
        self.hide()
        if hasattr(self, "tray_icon"):
            self.tray_icon.showMessage(
                "GrizzyClaw",
                "Running in background. Click tray icon to restore.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
    
    def _cleanup_before_quit(self):
        """Stop background workers before exit to prevent macOS crash report."""
        # Signal Telegram bot to stop (runs in TelegramStartWorker thread)
        if self.telegram_bot and hasattr(self.telegram_bot, "_stop_event"):
            try:
                self.telegram_bot._stop_event.set()
            except Exception:
                pass
        # Wait for Telegram worker to finish (with timeout)
        if hasattr(self, "_telegram_worker") and self._telegram_worker and self._telegram_worker.isRunning():
            self._telegram_worker.wait(3000)
        # Wait for stop worker if token was being changed
        if hasattr(self, "_stop_worker") and self._stop_worker and self._stop_worker.isRunning():
            self._stop_worker.wait(2000)
        # Wait for chat MessageWorker if one is running
        if hasattr(self.chat_widget, "worker") and self.chat_widget.worker and self.chat_widget.worker.isRunning():
            self.chat_widget.worker.wait(2000)

    def quit_app(self):
        if hasattr(self, "tray_icon"):
            self.tray_icon.hide()
        QApplication.quit()
    
    def closeEvent(self, event):
        event.ignore()
        self.hide_to_tray()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("GrizzyClaw")
    app.setApplicationVersion("0.1.0")
    app.setStyle("Fusion")
    
    # Load settings to apply appearance before creating window
    settings = Settings()
    config_path = get_config_path()
    if config_path.exists():
        try:
            settings = Settings.from_file(str(config_path))
        except Exception:
            pass
    
    # Apply font settings
    font_family = settings.font_family
    if font_family == "System Default":
        font_family = "-apple-system"
    font = QFont(font_family, settings.font_size)
    app.setFont(font)
    
    window = GrizzyClawApp()
    window.show()

    exit_code = app.exec()
    # Use os._exit(0) for normal quit to bypass Python shutdown - avoids macOS
    # "quit unexpectedly" crash (often caused by Qt/PyInstaller during teardown)
    if exit_code == 0:
        os._exit(0)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
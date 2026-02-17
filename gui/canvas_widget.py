"""Live Visual Canvas - displays images, diagrams, and A2UI agent-generated content"""

from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QFrame,
    QScrollArea,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QPixmap


class CanvasWidget(QWidget):
    """Visual canvas for displaying images and visual content."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QLabel("Visual Canvas")
        header.setFont(QFont("-apple-system", 16, QFont.Weight.Bold))
        header.setStyleSheet("color: #1C1C1E;")
        layout.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("""
            QScrollArea {
                border: 1px solid #E5E5EA;
                border-radius: 8px;
                background: #FAFAFA;
            }
        """)
        self.scroll.setMinimumHeight(360)

        self.content = QWidget()
        self.content.setMinimumHeight(320)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(24, 24, 24, 24)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content_layout.setSpacing(12)

        self.placeholder = QLabel(
            "Images from browser screenshots,\n"
            "attachments, or generated content\n"
            "will appear here."
        )
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setFont(QFont("-apple-system", 13))
        self.placeholder.setMinimumHeight(260)
        self.placeholder.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self.placeholder.setStyleSheet(
            "color: #8E8E93; padding: 80px 48px;"
        )
        self.placeholder.setWordWrap(True)
        self.content_layout.addWidget(self.placeholder)

        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

    def display_image(self, path: str) -> bool:
        """Display an image from file path."""
        p = Path(path)
        if not p.exists():
            return False
        pixmap = QPixmap(str(p))
        if pixmap.isNull():
            return False
        self.placeholder.hide()
        label = QLabel()
        label.setPixmap(pixmap.scaled(600, 400, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content_layout.insertWidget(0, label)
        return True

    def display_pixmap(self, pixmap: QPixmap) -> None:
        """Display a QPixmap directly."""
        if pixmap.isNull():
            return
        self.placeholder.hide()
        label = QLabel()
        label.setPixmap(pixmap.scaled(600, 400, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content_layout.insertWidget(0, label)

    def display_a2ui(self, payload: str | Dict[str, Any]) -> bool:
        """Render A2UI (Agent-to-User Interface) JSON payload on the canvas."""
        from .a2ui_renderer import render_a2ui

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        if render_a2ui(payload, container, layout):
            self.placeholder.hide()
            self.content_layout.insertWidget(0, container)
            return True
        return False

    def clear(self) -> None:
        """Clear the canvas."""
        while self.content_layout.count() > 1:
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.placeholder.show()

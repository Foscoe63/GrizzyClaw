"""Live Visual Canvas - displays images, diagrams, and A2UI agent-generated content"""

from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QFileDialog,
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

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header = QLabel("Visual Canvas")
        header.setFont(QFont("-apple-system", 16, QFont.Weight.Bold))
        header.setStyleSheet("color: #1C1C1E;")
        header_row.addWidget(header)
        header_row.addStretch()
        _btn_style = (
            "QPushButton { background-color: #E5E5EA; color: #1C1C1E; border: none; "
            "border-radius: 6px; padding: 6px 12px; font-size: 13px; }"
            "QPushButton:hover { background-color: #D1D1D6; }"
            "QPushButton:pressed { background-color: #C6C6C8; }"
        )
        self.load_btn = QPushButton("Load")
        self.load_btn.setStyleSheet(_btn_style)
        self.load_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.load_btn.setToolTip("Load an image from file")
        self.load_btn.clicked.connect(self._load_canvas)
        header_row.addWidget(self.load_btn)
        self.save_btn = QPushButton("Save")
        self.save_btn.setStyleSheet(_btn_style)
        self.save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_btn.setToolTip("Save canvas content as PNG")
        self.save_btn.clicked.connect(self._save_canvas)
        header_row.addWidget(self.save_btn)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setStyleSheet(_btn_style)
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.clicked.connect(self.clear)
        header_row.addWidget(self.clear_btn)
        layout.addLayout(header_row)

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
        self.content_layout.setContentsMargins(24, 32, 24, 32)
        self.content_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.content_layout.setSpacing(12)

        self.placeholder = QLabel(
            "Images (screenshots, attachments),\n"
            "A2UI cards/diagrams, and inline images\n"
            "will appear here."
        )
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setFont(QFont("-apple-system", 13))
        self.placeholder.setMinimumHeight(260)
        self.placeholder.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self.placeholder.setStyleSheet(
            "color: #8E8E93; padding: 48px 48px 48px 48px; margin: 12px 0;"
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
        self.content.update()
        self.scroll.viewport().update()
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

    def _save_canvas(self) -> None:
        """Save current canvas content as a PNG image."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Canvas",
            "",
            "PNG (*.png);;All Files (*)",
        )
        if not path:
            return
        pixmap = self.content.grab()
        if pixmap.isNull():
            return
        if not pixmap.save(path):
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self,
                "Save Canvas",
                "Could not save the image to the selected path.",
            )

    def _load_canvas(self) -> None:
        """Load an image from file and display it on the canvas."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Image",
            "",
            "Images (*.png *.jpg *.jpeg *.gif *.webp);;All Files (*)",
        )
        if not path:
            return
        self.clear()
        self.display_image(path)

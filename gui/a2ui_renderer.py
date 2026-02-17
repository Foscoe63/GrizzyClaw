"""A2UI (Agent-to-User Interface) renderer - declarative UI from agent payloads

Renders A2UI JSON format (https://a2ui.org) into Qt widgets for the Live Canvas.
Supports: text, image, button, and basic layout components.
"""

import base64
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)


def render_a2ui(
    payload: str | Dict[str, Any],
    parent: QWidget,
    layout: QVBoxLayout,
    on_button_click: Optional[callable] = None,
) -> bool:
    """
    Parse A2UI JSON and add widgets to the given layout.

    Args:
        payload: JSON string or dict with A2UI Response format
        parent: Parent widget for created widgets
        layout: Layout to add widgets to
        on_button_click: Optional callback(component_id, label) for button clicks

    Returns:
        True if at least one component was rendered
    """
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as e:
            logger.warning(f"A2UI parse error: {e}")
            return False
    else:
        data = payload

    # A2UI format: { "components": [...], "dataModel": {...} }
    components = data.get("components", data.get("content", []))
    if not components:
        return False

    rendered = 0
    for comp in components:
        ctype = comp.get("type", comp.get("componentType", ""))
        cid = comp.get("id", "")
        props = comp.get("properties", comp.get("props", {}))

        if ctype in ("text", "Text"):
            text = props.get("text", props.get("content", ""))
            if text:
                lbl = QLabel(text)
                lbl.setWordWrap(True)
                lbl.setStyleSheet("color: #1C1C1E; font-size: 14px; padding: 8px;")
                layout.addWidget(lbl)
                rendered += 1

        elif ctype in ("image", "Image"):
            src = props.get("src", props.get("url", props.get("source", "")))
            if src:
                pixmap = _load_image(src)
                if not pixmap.isNull():
                    lbl = QLabel()
                    lbl.setPixmap(pixmap.scaled(400, 300, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    layout.addWidget(lbl)
                    rendered += 1

        elif ctype in ("button", "Button"):
            label = props.get("label", props.get("text", "Click"))
            btn = QPushButton(label)
            btn.setProperty("a2ui_id", cid)
            if on_button_click:
                _cid, _label = cid, label
                btn.clicked.connect(lambda: on_button_click(_cid, _label))
            layout.addWidget(btn)
            rendered += 1

        elif ctype in ("card", "Card"):
            # Recursive: render children
            children = comp.get("children", comp.get("content", []))
            if children:
                sub = QWidget()
                sub_layout = QVBoxLayout(sub)
                sub_layout.setContentsMargins(12, 12, 12, 12)
                render_a2ui({"components": children}, parent, sub_layout, on_button_click)
                layout.addWidget(sub)
                rendered += 1

    return rendered > 0


def _load_image(src: str) -> QPixmap:
    """Load image from URL, file path, or base64 data URI."""
    pix = QPixmap()
    if src.startswith("data:"):
        # data:image/png;base64,...
        try:
            header, b64 = src.split(",", 1)
            data = base64.b64decode(b64)
            pix.loadFromData(data)
        except Exception:
            pass
    elif src.startswith(("http://", "https://")):
        try:
            import urllib.request
            with urllib.request.urlopen(src, timeout=10) as r:
                data = r.read()
            pix.loadFromData(data)
        except Exception:
            pass
    else:
        p = Path(src).expanduser()
        if p.exists():
            pix.load(str(p))
    return pix

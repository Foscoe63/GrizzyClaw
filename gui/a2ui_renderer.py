"""A2UI (Agent-to-User Interface) renderer for the Visual Canvas.

Renders structured JSON payloads into Qt widgets: cards, text blocks,
key-value lists, sections, and simple diagrams.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFrame,
    QScrollArea,
    QSizePolicy,
    QGridLayout,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

logger = logging.getLogger(__name__)

# Default styling to match canvas
_CARD_STYLE = """
    QFrame {
        background-color: #FFFFFF;
        border: 1px solid #E5E5EA;
        border-radius: 8px;
        padding: 12px;
    }
"""
_SECTION_TITLE_STYLE = "color: #1C1C1E; font-weight: bold; font-size: 14px; margin-bottom: 8px;"
_LABEL_STYLE = "color: #3C3C43; font-size: 13px;"
_KEY_STYLE = "color: #8E8E93; font-size: 12px;"
_DIAGRAM_NODE_STYLE = """
    QFrame {
        background-color: #F2F2F7;
        border: 1px solid #C6C6C8;
        border-radius: 6px;
        padding: 8px 12px;
    }
"""


def _add_card(container: QWidget, layout: QVBoxLayout, spec: Dict[str, Any]) -> bool:
    title = spec.get("title", "")
    content = spec.get("content", "")
    frame = QFrame()
    frame.setStyleSheet(_CARD_STYLE)
    frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    card_layout = QVBoxLayout(frame)
    card_layout.setContentsMargins(12, 12, 12, 12)
    if title:
        title_label = QLabel(title)
        title_label.setStyleSheet(_SECTION_TITLE_STYLE)
        title_label.setWordWrap(True)
        card_layout.addWidget(title_label)
    if content:
        content_label = QLabel(content)
        content_label.setStyleSheet(_LABEL_STYLE)
        content_label.setWordWrap(True)
        content_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        card_layout.addWidget(content_label)
    layout.addWidget(frame)
    return True


def _add_text(container: QWidget, layout: QVBoxLayout, spec: Dict[str, Any]) -> bool:
    content = spec.get("content", str(spec))
    label = QLabel(content)
    label.setStyleSheet(_LABEL_STYLE)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(label)
    return True


def _add_key_value(container: QWidget, layout: QVBoxLayout, spec: Dict[str, Any]) -> bool:
    items = spec.get("items", spec.get("entries", []))
    if not items:
        return False
    frame = QFrame()
    frame.setStyleSheet(_CARD_STYLE)
    frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    grid = QGridLayout(frame)
    for i, entry in enumerate(items):
        if isinstance(entry, dict):
            k = entry.get("key", entry.get("name", ""))
            v = entry.get("value", entry.get("val", ""))
        else:
            k, v = str(entry), ""
        key_label = QLabel(f"{k}:")
        key_label.setStyleSheet(_KEY_STYLE)
        val_label = QLabel(str(v))
        val_label.setStyleSheet(_LABEL_STYLE)
        val_label.setWordWrap(True)
        val_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(key_label, i, 0)
        grid.addWidget(val_label, i, 1)
    layout.addWidget(frame)
    return True


def _add_section(container: QWidget, layout: QVBoxLayout, spec: Dict[str, Any]) -> bool:
    title = spec.get("title", "")
    children = spec.get("children", spec.get("items", []))
    if title:
        title_label = QLabel(title)
        title_label.setStyleSheet(_SECTION_TITLE_STYLE)
        title_label.setWordWrap(True)
        layout.addWidget(title_label)
    inner = QVBoxLayout()
    inner.setContentsMargins(0, 4, 0, 0)
    for child in children:
        if isinstance(child, dict):
            render_a2ui(child, container, inner)
    layout.addLayout(inner)
    return True


def _add_diagram(container: QWidget, layout: QVBoxLayout, spec: Dict[str, Any]) -> bool:
    """Simple diagram: nodes and edges rendered as labeled boxes and arrows."""
    nodes = spec.get("nodes", [])
    edges = spec.get("edges", [])
    node_map: Dict[str, str] = {}
    for n in nodes:
        if isinstance(n, dict):
            node_map[str(n.get("id", ""))] = str(n.get("label", n.get("id", "")))
        else:
            node_map[str(n)] = str(n)
    if not node_map and not edges:
        return False
    frame = QFrame()
    frame.setStyleSheet("QFrame { background-color: #FAFAFA; border-radius: 8px; padding: 16px; }")
    frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    diag_layout = QVBoxLayout(frame)
    for nid, label in node_map.items():
        node_frame = QFrame()
        node_frame.setStyleSheet(_DIAGRAM_NODE_STYLE)
        node_frame.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        lbl = QLabel(label)
        lbl.setStyleSheet(_LABEL_STYLE)
        node_inner = QVBoxLayout(node_frame)
        node_inner.addWidget(lbl)
        diag_layout.addWidget(node_frame)
    if edges:
        parts = []
        for e in edges[:15]:
            if isinstance(e, dict):
                fr = node_map.get(str(e.get("from", "")), str(e.get("from", "")))
                to = node_map.get(str(e.get("to", "")), str(e.get("to", "")))
                parts.append(f"{fr} → {to}")
            else:
                parts.append(str(e))
        if parts:
            arrows_label = QLabel("  |  ".join(parts))
            arrows_label.setStyleSheet(_KEY_STYLE)
            arrows_label.setWordWrap(True)
            diag_layout.addWidget(arrows_label)
    layout.addWidget(frame)
    return True


def render_a2ui(payload: str | Dict[str, Any], container: QWidget, layout: QVBoxLayout) -> bool:
    """Render an A2UI payload into the given container/layout.

    Payload can be a JSON string or a dict. Supported types:
    - card: { "type": "card", "title": "...", "content": "..." }
    - text: { "type": "text", "content": "..." }
    - key_value: { "type": "key_value", "items": [ {"key": "k", "value": "v"}, ... ] }
    - section: { "type": "section", "title": "...", "children": [ ... ] }
    - diagram: { "type": "diagram", "nodes": [ {"id": "a", "label": "A"}, ... ], "edges": [ {"from": "a", "to": "b"}, ... ] }

    Returns True if something was rendered, False otherwise.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as e:
            logger.warning("A2UI invalid JSON: %s", e)
            return False
    if not isinstance(payload, dict):
        return False
    kind = (payload.get("type") or payload.get("kind", "")).lower()
    if kind == "card":
        return _add_card(container, layout, payload)
    if kind == "text":
        return _add_text(container, layout, payload)
    if kind == "key_value":
        return _add_key_value(container, layout, payload)
    if kind == "section":
        return _add_section(container, layout, payload)
    if kind == "diagram":
        return _add_diagram(container, layout, payload)
    # Default: treat as card with content = str(payload) or single text
    if not kind:
        return _add_text(container, layout, {"content": payload.get("content", json.dumps(payload))})
    logger.warning("A2UI unknown type: %s", kind)
    return False

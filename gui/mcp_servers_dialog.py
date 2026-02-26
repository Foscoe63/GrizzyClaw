import json
import os
import subprocess
import signal
import psutil
from pathlib import Path
from typing import Optional, Dict, List
import time
from datetime import datetime

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QSpinBox, QCheckBox, QPushButton, QMessageBox, QTextEdit, QFormLayout,
    QTreeWidget, QTreeWidgetItem, QWidget, QScrollArea, QFrame, QInputDialog,
    QHeaderView, QPlainTextEdit, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread
from PyQt6.QtGui import QFont, QShowEvent

from grizzyclaw.mcp_client import (
    invalidate_tools_cache,
    call_mcp_tool,
    validate_server_config,
    discover_one_server,
    discover_mcp_servers_zeroconf,
    normalize_mcp_args,
)

# HTTP utils for remote checks and marketplace
import ssl
try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT = ssl.create_default_context()
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

# MCP Marketplace Sources
MCP_MARKETPLACE_SOURCES = {
    "Built-in Marketplace": None,  # Uses DEFAULT_MCP_MARKETPLACE
    "awesome-mcp-servers (GitHub)": "https://raw.githubusercontent.com/appcypher/awesome-mcp-servers/main/README.md",
    "Official MCP Examples": "https://modelcontextprotocol.io/examples",
    "mcp-awesome.com": "https://mcp-awesome.com/api/servers",
    "mcpservers.org": "https://mcpservers.org/api/servers.json",
    "mcplist.ai": "https://mcplist.ai/api/servers",
    "mcp.so": "https://mcp.so/api/servers",
    "mcpnodes.com": "https://mcpnodes.com/api/servers.json",
    "agentmcp.net": "https://agentmcp.net/api/servers",
    "Custom URL...": "custom",
}


class ValidateConfigWorker(QThread):
    """Run MCP validate_server_config in a background thread to avoid blocking/crashing the GUI."""
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, config: dict):
        super().__init__()
        self.config = config

    def run(self):
        try:
            import asyncio
            ok, msg = asyncio.run(validate_server_config(self.config))
            self.finished_signal.emit(ok, msg)
        except Exception as e:
            self.finished_signal.emit(False, str(e))

class MCPTab(QWidget):
    """MCP servers only (add/edit/test, marketplace, refresh)."""
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.running_processes = {}
        self.recently_started = {}
        self.started_servers_file = Path.home() / ".grizzyclaw" / "mcp_started.json"
        self.mcp_errors_file = Path.home() / ".grizzyclaw" / "mcp_errors.json"
        self.mcp_last_error = {}
        self._load_error_history()
        self.is_dark = False
        theme = getattr(self.settings, "theme", "Light")
        self.is_dark = theme in ["Dark", "High Contrast Dark", "Dracula", "Monokai", "Nord", "Solarized Dark"]
        self._setup_theme_colors()
        self._load_started_servers()
        self._tools_workers = {}
        self.setup_ui()

    def _load_error_history(self):
        """Load persisted error history from disk."""
        try:
            if self.mcp_errors_file.exists():
                data = json.loads(self.mcp_errors_file.read_text())
                self.mcp_last_error = data.get("errors", {})
        except Exception:
            self.mcp_last_error = {}

    def _save_error_history(self):
        """Persist error history to disk (last 5 errors per server)."""
        try:
            self.mcp_errors_file.parent.mkdir(parents=True, exist_ok=True)
            # Keep only last 5 errors per server
            trimmed = {}
            for name, errors in self.mcp_last_error.items():
                if isinstance(errors, list):
                    trimmed[name] = errors[-5:]
                elif isinstance(errors, str):
                    trimmed[name] = [{"error": errors, "time": datetime.now().isoformat()}]
            self.mcp_errors_file.write_text(json.dumps({"errors": trimmed}, indent=2))
        except Exception:
            pass

    def _record_error(self, server_name: str, error: str):
        """Record an error for a server with timestamp."""
        if server_name not in self.mcp_last_error:
            self.mcp_last_error[server_name] = []
        if isinstance(self.mcp_last_error[server_name], str):
            self.mcp_last_error[server_name] = [{"error": self.mcp_last_error[server_name], "time": datetime.now().isoformat()}]
        self.mcp_last_error[server_name].append({"error": error, "time": datetime.now().isoformat()})
        self._save_error_history()

    def _clear_error(self, server_name: str):
        """Clear errors for a server on success."""
        self.mcp_last_error.pop(server_name, None)
        self._save_error_history()

    def _get_last_error(self, server_name: str) -> str:
        """Get the most recent error for display."""
        errors = self.mcp_last_error.get(server_name, [])
        if isinstance(errors, str):
            return errors
        if isinstance(errors, list) and errors:
            return errors[-1].get("error", "")
        return ""

    def showEvent(self, event: QShowEvent):
        super().showEvent(event)
        self.refresh_mcp_statuses()
        # Auto-start servers that were running when the app was last closed (once per launch)
        QTimer.singleShot(400, self._auto_start_saved_servers)

    def _setup_theme_colors(self):
        if self.is_dark:
            self.bg_color = "#1E1E1E"
            self.fg_color = "#FFFFFF"
            self.card_bg = "#2D2D2D"
            self.border_color = "#3A3A3C"
            self.input_bg = "#3A3A3C"
            self.accent_color = "#0A84FF"
            self.secondary_text = "#8E8E93"
            self.hover_bg = "#3A3A3C"
            self.alt_row_bg = "#252525"
        else:
            self.bg_color = "#FFFFFF"
            self.fg_color = "#1C1C1E"
            self.card_bg = "#FAFAFA"
            self.border_color = "#E5E5EA"
            self.input_bg = "#FFFFFF"
            self.accent_color = "#007AFF"
            self.secondary_text = "#8E8E93"
            self.hover_bg = "#F5F5F7"
            self.alt_row_bg = "#FAFAFA"

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)
        self.setStyleSheet(f"background-color: {self.bg_color};")
        header = QLabel("MCP Servers")
        header.setFont(QFont("-apple-system", 18, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {self.fg_color}; background: transparent;")
        layout.addWidget(header)
        subtitle = QLabel("Model Context Protocol servers for extended tools.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {self.secondary_text}; font-size: 13px; margin-bottom: 8px; background: transparent;")
        layout.addWidget(subtitle)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 8, 0)
        scroll_layout.setSpacing(20)
        mcp_card = self._create_card("🔌 MCP Servers", "Add and manage MCP servers for tools (search, filesystem, etc.)")
        mcp_layout = mcp_card.layout()
        
        mcp_marketplace_row = QHBoxLayout()
        mcp_marketplace_row.addWidget(QLabel("MCP Marketplace URL:"))
        self.mcp_marketplace_url = QLineEdit(getattr(self.settings, "mcp_marketplace_url", None) or "")
        self.mcp_marketplace_url.setPlaceholderText("Optional: JSON URL to auto-discover ClawHub MCP servers")
        self.mcp_marketplace_url.setStyleSheet(self._input_style())
        self.mcp_marketplace_url.setFixedHeight(32)
        mcp_marketplace_row.addWidget(self.mcp_marketplace_url)
        mcp_layout.addLayout(mcp_marketplace_row)
        mcp_marketplace_hint = QLabel("Leave empty to use built-in list. In chat, use skill mcp_marketplace → discover / install.")
        mcp_marketplace_hint.setStyleSheet(f"color: {self.secondary_text}; font-size: 11px;")
        mcp_layout.addWidget(mcp_marketplace_hint)
        docs_link = QLabel(
            '<a href="https://modelcontextprotocol.io/introduction">How to add MCP servers</a>'
        )
        docs_link.setOpenExternalLinks(True)
        docs_link.setStyleSheet(f"color: {self.accent_color}; font-size: 12px;")
        mcp_layout.addWidget(docs_link)
        
        # Server list: 4 columns (Server, Status, Tools, Test)
        self.mcp_servers_tree = QTreeWidget()
        self.mcp_servers_tree.setHeaderLabels(["Server", "Status", "Tools", "Test"])
        header = self.mcp_servers_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.mcp_servers_tree.setColumnWidth(1, 44)
        self.mcp_servers_tree.setColumnWidth(2, 58)
        self.mcp_servers_tree.setColumnWidth(3, 52)
        self.mcp_servers_tree.setMinimumHeight(200)
        self.mcp_servers_tree.setMaximumHeight(320)
        self.mcp_servers_tree.setUniformRowHeights(True)
        self.mcp_servers_tree.setAlternatingRowColors(True)
        self.mcp_servers_tree.setRootIsDecorated(False)
        self.mcp_servers_tree.setIndentation(0)
        self.mcp_servers_tree.setStyleSheet(self._tree_style())
        mcp_layout.addWidget(self.mcp_servers_tree)
        
        # Status legend
        legend = QLabel(
            "🟢 Running  🔴 Stopped  •  Click status to toggle. "
            "Local (stdio) servers are started automatically by the agent when you use their tools in chat; keeping them running here is optional."
        )
        legend.setStyleSheet(f"color: {self.secondary_text}; font-size: 11px; padding: 4px 0; background: transparent;")
        mcp_layout.addWidget(legend)
        
        # Quick add: paste URL or command
        quick_row = QHBoxLayout()
        self.quick_add_input = QLineEdit()
        self.quick_add_input.setPlaceholderText("Paste URL or command (e.g. npx -y @modelcontextprotocol/server-foo or https://...)")
        self.quick_add_input.setStyleSheet(self._input_style())
        self.quick_add_input.setMinimumWidth(320)
        quick_row.addWidget(self.quick_add_input)
        quick_add_btn = QPushButton("Quick add")
        quick_add_btn.setToolTip("Parse URL or command and open Add Server with suggested name and config; Test before saving.")
        quick_add_btn.clicked.connect(self.quick_add_mcp)
        quick_add_btn.setStyleSheet(self._secondary_btn_style())
        quick_row.addWidget(quick_add_btn)
        quick_row.addStretch()
        mcp_layout.addLayout(quick_row)

        # Button row
        mcp_btns = QHBoxLayout()
        mcp_btns.setSpacing(8)
        
        add_btn = QPushButton("+ Add Server")
        add_btn.clicked.connect(self.add_mcp)
        add_btn.setStyleSheet(self._primary_btn_style())
        mcp_btns.addWidget(add_btn)
        
        # Marketplace dropdown menu
        self.marketplace_combo = QComboBox()
        self.marketplace_combo.addItem("📦 Add from Marketplace...")
        self.marketplace_combo.addItem("── Built-in ──", None)
        self.marketplace_combo.addItem("  Built-in Marketplace", None)
        self.marketplace_combo.addItem("── GitHub Repositories ──", None)
        self.marketplace_combo.addItem("  awesome-mcp-servers", "https://api.github.com/repos/appcypher/awesome-mcp-servers/contents")
        self.marketplace_combo.addItem("  Official MCP Examples", "https://modelcontextprotocol.io/examples")
        self.marketplace_combo.addItem("── Web Directories ──", None)
        self.marketplace_combo.addItem("  mcp-awesome.com (1200+ servers)", "mcp-awesome")
        self.marketplace_combo.addItem("  mcpservers.org", "mcpservers.org")
        self.marketplace_combo.addItem("  mcplist.ai", "mcplist.ai")
        self.marketplace_combo.addItem("  mcp.so", "mcp.so")
        self.marketplace_combo.addItem("  mcpnodes.com", "mcpnodes.com")
        self.marketplace_combo.addItem("  agentmcp.net", "agentmcp.net")
        self.marketplace_combo.addItem("── Custom ──", None)
        self.marketplace_combo.addItem("  Enter custom URL...", "custom")
        self.marketplace_combo.setFixedHeight(32)
        self.marketplace_combo.setMinimumWidth(200)
        self.marketplace_combo.setStyleSheet(self._combo_style())
        self.marketplace_combo.setToolTip("Select a marketplace source to browse and install MCP servers")
        self.marketplace_combo.currentIndexChanged.connect(self._on_marketplace_selected)
        mcp_btns.addWidget(self.marketplace_combo)
        
        discover_btn = QPushButton("Discover on network")
        discover_btn.clicked.connect(self.discover_mcp_network)
        discover_btn.setStyleSheet(self._secondary_btn_style())
        discover_btn.setToolTip("Find MCP servers on the local network (mDNS / ZeroConf; servers must advertise _mcp._tcp.local.)")
        mcp_btns.addWidget(discover_btn)
        
        edit_btn = QPushButton("Edit")
        edit_btn.clicked.connect(self.edit_mcp)
        edit_btn.setStyleSheet(self._secondary_btn_style())
        mcp_btns.addWidget(edit_btn)
        
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self.remove_mcp)
        remove_btn.setStyleSheet(self._secondary_btn_style())
        mcp_btns.addWidget(remove_btn)
        
        mcp_btns.addStretch()
        
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self._on_refresh_mcp)
        refresh_btn.setToolTip("Refresh status and invalidate tool discovery cache")
        refresh_btn.setStyleSheet(self._secondary_btn_style())
        mcp_btns.addWidget(refresh_btn)
        
        test_btn = QPushButton("🧪 Test All")
        test_btn.clicked.connect(self.test_mcp)
        test_btn.setStyleSheet(self._secondary_btn_style())
        mcp_btns.addWidget(test_btn)
        
        errors_btn = QPushButton("📋 Error Log")
        errors_btn.clicked.connect(self._show_error_history)
        errors_btn.setToolTip("View recent errors for all MCP servers")
        errors_btn.setStyleSheet(self._secondary_btn_style())
        mcp_btns.addWidget(errors_btn)
        
        mcp_layout.addLayout(mcp_btns)
        scroll_layout.addWidget(mcp_card)
        
        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)
        
        # Initialize MCP data
        self.mcp_file = Path(self.settings.mcp_servers_file).expanduser()
        self.mcp_servers_data = []
        self.load_mcp_list()
    
    def _create_card(self, title, description):
        """Create a styled card widget"""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {self.card_bg};
                border: 1px solid {self.border_color};
                border-radius: 12px;
                padding: 16px;
            }}
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        
        title_label = QLabel(title)
        title_label.setFont(QFont("-apple-system", 15, QFont.Weight.DemiBold))
        title_label.setStyleSheet(f"color: {self.fg_color}; background: transparent;")
        layout.addWidget(title_label)
        
        if description:
            desc_label = QLabel(description)
            desc_label.setStyleSheet(f"color: {self.secondary_text}; font-size: 12px; background: transparent;")
            layout.addWidget(desc_label)
        
        return card
    
    def _input_style(self):
        return f"""
            QLineEdit {{
                padding: 0 12px;
                border: 1px solid {self.border_color};
                border-radius: 8px;
                background: {self.input_bg};
                color: {self.fg_color};
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border: 2px solid {self.accent_color};
            }}
        """
    
    def _list_style(self):
        return f"""
            QListWidget {{
                border: 1px solid {self.border_color};
                border-radius: 8px;
                background: {self.input_bg};
                color: {self.fg_color};
                padding: 4px;
            }}
            QListWidget::item {{
                padding: 8px 12px;
                border-radius: 4px;
                color: {self.fg_color};
            }}
            QListWidget::item:selected {{
                background: {self.accent_color};
                color: white;
            }}
            QListWidget::item:hover:!selected {{
                background: {self.hover_bg};
            }}
        """
    
    def _tree_style(self):
        header_bg = '#3A3A3C' if self.is_dark else '#F5F5F7'
        header_border = '#48484A' if self.is_dark else '#E5E5EA'
        row_border = '#3A3A3C' if self.is_dark else '#F0F0F0'
        selected_bg = '#0A84FF' if self.is_dark else '#E3F2FD'
        selected_fg = '#FFFFFF' if self.is_dark else '#1C1C1E'
        return f"""
            QTreeWidget {{
                border: 1px solid {self.border_color};
                border-radius: 8px;
                background: {self.input_bg};
                color: {self.fg_color};
                alternate-background-color: {self.alt_row_bg};
            }}
            QTreeWidget::item {{
                height: 32px;
                padding: 0 10px;
                border-bottom: 1px solid {row_border};
                color: {self.fg_color};
            }}
            QTreeWidget::item:selected {{
                background: {selected_bg};
                color: {selected_fg};
            }}
            QTreeWidget::item:hover:!selected {{
                background: {self.hover_bg};
            }}
            QHeaderView::section {{
                background: {header_bg};
                border: none;
                border-bottom: 1px solid {header_border};
                padding: 6px 8px;
                font-weight: 600;
                font-size: 11px;
                color: {self.secondary_text};
            }}
        """
    
    def _primary_btn_style(self):
        return f"""
            QPushButton {{
                background: {self.accent_color};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 16px;
                font-weight: 600;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background: {'#0056CC' if not self.is_dark else '#0A6FE8'};
            }}
            QPushButton:pressed {{
                background: {'#004099' if not self.is_dark else '#0860D0'};
            }}
        """
    
    def _secondary_btn_style(self):
        btn_bg = '#3A3A3C' if self.is_dark else '#F5F5F7'
        btn_hover = '#48484A' if self.is_dark else '#E5E5EA'
        btn_pressed = '#555555' if self.is_dark else '#D1D1D6'
        return f"""
            QPushButton {{
                background: {btn_bg};
                color: {self.fg_color};
                border: 1px solid {self.border_color};
                border-radius: 8px;
                padding: 8px 16px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background: {btn_hover};
            }}
            QPushButton:pressed {{
                background: {btn_pressed};
            }}
        """
    
    def _icon_btn_style(self):
        btn_bg = '#3A3A3C' if self.is_dark else '#F5F5F7'
        btn_hover = '#48484A' if self.is_dark else '#E5E5EA'
        return f"""
            QPushButton {{
                background: {btn_bg};
                border: 1px solid {self.border_color};
                border-radius: 8px;
                font-size: 14px;
                color: {self.fg_color};
            }}
            QPushButton:hover {{
                background: {btn_hover};
            }}
            QPushButton:checked {{
                background: {self.accent_color};
                color: white;
                border-color: {self.accent_color};
            }}
        """
    
    def _combo_style(self):
        return f"""
            QComboBox {{
                padding: 4px 12px;
                border: 1px solid {self.border_color};
                border-radius: 8px;
                background: {self.card_bg};
                color: {self.fg_color};
                font-size: 13px;
            }}
            QComboBox:hover {{
                border: 1px solid {self.accent_color};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 20px;
            }}
            QComboBox QAbstractItemView {{
                background: {self.card_bg};
                border: 1px solid {self.border_color};
                selection-background-color: {self.accent_color};
            }}
        """
    
    def test_mcp(self):
        mcp_count = len(self.mcp_servers_data)
        running_status = []
        for i, s in enumerate(self.mcp_servers_data):
            name = s.get("name", f"MCP {i}")
            if s.get("url"):
                status = "✓ running" if self._test_remote_connection(s) == "✓" else "✗ stopped"
            elif s.get("command"):
                running = self._check_server_running_by_ps(s)
                status = "✓ running" if running else "✗ stopped"
            else:
                status = "—"
            running_status.append(f"  {name}: {status}")
        running_count = len([r for r in running_status if "✓ running" in r])
        names_str = ", ".join([s.get("name", str(i)) for i, s in enumerate(self.mcp_servers_data)][:5]) or "none"
        msg = f"""MCP File: {self.mcp_file}
Configured: {mcp_count}
Running: {running_count}/{mcp_count}

Status:
{chr(10).join(running_status)}

Names: {names_str}"""
        QMessageBox.information(self, "Test MCP", msg)

    def load_mcp_list(self):
        self.mcp_servers_tree.clear()
        self.mcp_servers_data = self._load_mcp_data()
        for server in self.mcp_servers_data:
            display_name = server.get("name", "Unnamed MCP")
            if server.get("url"):
                display_name += " 🌐"  # Remote indicator
            item = QTreeWidgetItem([display_name, "", "", ""])
            item.setData(0, 32, json.dumps(server))
            self.mcp_servers_tree.addTopLevelItem(item)

            btn = QPushButton("🔴")
            btn.setFixedSize(32, 26)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            srv = server.copy()
            btn.clicked.connect(lambda checked=False, s=srv: self.toggle_mcp_connection(s))
            btn.setStyleSheet(self._stopped_btn_style())
            self.mcp_servers_tree.setItemWidget(item, 1, btn)

            tools_label = QLabel("--")
            tools_label.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            tools_label.setFixedSize(52, 26)
            tools_label.setStyleSheet(self._tools_label_style())
            self.mcp_servers_tree.setItemWidget(item, 2, tools_label)

            test_btn = QPushButton("Test")
            test_btn.setFixedSize(48, 26)
            test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            test_btn.setStyleSheet(self._secondary_btn_style())
            test_btn.clicked.connect(lambda checked=False, it=item: self._test_one_server(it))
            self.mcp_servers_tree.setItemWidget(item, 3, test_btn)
        self.refresh_mcp_statuses()

    def _mcp_cell_widget(self, item: QTreeWidgetItem, column: int) -> QWidget | None:
        """Return the widget for column 1 (button) or 2 (tools label)."""
        return self.mcp_servers_tree.itemWidget(item, column)

    def _stopped_btn_style(self):
        """Style for stopped (red) status button"""
        if self.is_dark:
            return """
                QPushButton {
                    background-color: #3D2020;
                    border: 1px solid #5D3030;
                    border-radius: 4px;
                    font-size: 10px;
                    padding: 2px;
                }
                QPushButton:hover {
                    background-color: #4D2828;
                    border-color: #6D3838;
                }
            """
        else:
            return """
                QPushButton {
                    background-color: #FFEBEE;
                    border: 1px solid #EF9A9A;
                    border-radius: 4px;
                    font-size: 10px;
                    padding: 2px;
                }
                QPushButton:hover {
                    background-color: #FFCDD2;
                    border-color: #EF5350;
                }
            """
    
    def _running_btn_style(self):
        """Style for running (green) status button"""
        if self.is_dark:
            return """
                QPushButton {
                    background-color: #1B3D1B;
                    border: 1px solid #2D5D2D;
                    border-radius: 4px;
                    font-size: 10px;
                    padding: 2px;
                }
                QPushButton:hover {
                    background-color: #254D25;
                    border-color: #3D6D3D;
                }
            """
        else:
            return """
                QPushButton {
                    background-color: #E8F5E9;
                    border: 1px solid #A5D6A7;
                    border-radius: 4px;
                    font-size: 10px;
                    padding: 2px;
                }
                QPushButton:hover {
                    background-color: #C8E6C9;
                    border-color: #81C784;
                }
            """
    
    def _tools_label_style(self):
        """Style for tools count label (vertical alignment via wrapper layout)."""
        if self.is_dark:
            return """
                QLabel {
                    background: #1A3A5C;
                    color: #64B5F6;
                    border-radius: 3px;
                    padding: 5px 6px;
                    font-weight: 600;
                    font-size: 11px;
                }
            """
        else:
            return """
                QLabel {
                    background: #E3F2FD;
                    color: #1565C0;
                    border-radius: 3px;
                    padding: 5px 6px;
                    font-weight: 600;
                    font-size: 11px;
                }
            """

    @staticmethod
    def _parse_quick_add(text: str) -> Optional[Dict]:
        """Parse pasted input into suggested edit_data.
        Supports: full URL (remote), full command, npm slug (e.g. @org/pkg), pypi slug (pypi:pkg).
        Returns None if empty/unparseable.
        """
        t = (text or "").strip()
        if not t:
            return None
        # Remote URL
        if t.startswith("http://") or t.startswith("https://"):
            try:
                from urllib.parse import urlparse
                p = urlparse(t)
                name = (p.netloc or "remote").replace(".", "_").replace(":", "_") or "remote"
                return {"name": name, "url": t}
            except Exception:
                return {"name": "remote", "url": t}
        # npm-style or bare slug
        if t.startswith("@") or "/" in t:
            slug = t
            name = slug.split("/")[-1].split("@")[-1].replace("@", "").strip() or "mcp_server"
            return {"name": name, "command": "npx", "args": ["-y", slug]}
        # pypi style prefix
        if t.lower().startswith("pypi:"):
            pkg = t.split(":", 1)[-1].strip()
            name = (pkg or "mcp_server").split("[")[0]
            return {"name": name, "command": "uvx", "args": [pkg]}
        # Fallback: treat as command line
        parts = t.split()
        if not parts:
            return None
        cmd = parts[0]
        args = parts[1:] if len(parts) > 1 else []
        name = "mcp_server"
        for a in reversed(args or []):
            if "@" in a or "/" in a:
                name = a.split("/")[-1].split("@")[-1].replace("@", "").strip()
                break
        return {"name": name, "command": cmd, "args": args}

    def quick_add_mcp(self):
        text = self.quick_add_input.text().strip()
        edit_data = self._parse_quick_add(text)
        if not edit_data:
            QMessageBox.information(self, "Quick add", "Paste a URL (https://...) or command (e.g. npx -y @modelcontextprotocol/server-foo) first.")
            return
        dialog = MCPDialog(parent=self, edit_data=edit_data)
        if dialog.exec():
            config = dialog.get_config()
            self.mcp_servers_data.append(config)
            self._save_mcp_data()
            self.load_mcp_list()
            self.quick_add_input.clear()

    def add_mcp(self):
        dialog = MCPDialog(parent=self)
        if dialog.exec():
            config = dialog.get_config()
            self.mcp_servers_data.append(config)
            self._save_mcp_data()
            self.load_mcp_list()

    def edit_mcp(self):
        item = self.mcp_servers_tree.currentItem()
        if not item:
            QMessageBox.warning(self, "No Selection", "Select an MCP server to edit.")
            return
        data_str = item.data(0, 32)
        if not data_str:
            return
        try:
            data = json.loads(data_str)
        except ValueError:
            return
        dialog = MCPDialog(parent=self, edit_data=data)
        if dialog.exec():
            new_data = dialog.get_config()
            for j, d in enumerate(self.mcp_servers_data):
                if d.get('name') == data.get('name'):
                    self.mcp_servers_data[j] = new_data
                    break
            self._save_mcp_data()
            self.load_mcp_list()

    def remove_mcp(self):
        item = self.mcp_servers_tree.currentItem()
        if not item:
            return
        data_str = item.data(0, 32)
        try:
            data = json.loads(data_str)
            name = data.get('name', item.text(0))
        except ValueError:
            name = item.text(0)
        reply = QMessageBox.question(self, "Confirm Delete", f"Delete MCP server '{name}'?")
        if reply == QMessageBox.StandardButton.Yes:
            found = False
            for j, d in enumerate(self.mcp_servers_data):
                if d.get('name') == name:
                    del self.mcp_servers_data[j]
                    found = True
                    break
            if found:
                self._save_mcp_data()
            self.load_mcp_list()
    
    def _on_refresh_mcp(self):
        """Invalidate discovery cache then refresh status so next agent run gets fresh tools."""
        try:
            invalidate_tools_cache(self.mcp_file)
        except Exception:
            pass
        self.refresh_mcp_statuses()

    def _show_error_history(self):
        """Show a dialog with recent errors for all MCP servers."""
        dialog = QDialog(self)
        dialog.setWindowTitle("MCP Server Error Log")
        dialog.setMinimumSize(600, 400)
        dialog.setStyleSheet(f"background-color: {self.bg_color}; color: {self.fg_color};")
        
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        
        header = QLabel("📋 Recent MCP Server Errors")
        header.setFont(QFont("-apple-system", 16, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {self.fg_color}; background: transparent;")
        layout.addWidget(header)
        
        hint = QLabel("Errors are saved to ~/.grizzyclaw/mcp_errors.json and persisted across restarts.")
        hint.setStyleSheet(f"color: {self.secondary_text}; font-size: 11px; background: transparent;")
        layout.addWidget(hint)
        
        text_area = QPlainTextEdit()
        text_area.setReadOnly(True)
        text_area.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {self.card_bg};
                color: {self.fg_color};
                border: 1px solid {self.border_color};
                border-radius: 8px;
                font-family: monospace;
                font-size: 12px;
                padding: 12px;
            }}
        """)
        
        # Build error text
        if not self.mcp_last_error:
            text_area.setPlainText("✅ No errors recorded.\n\nErrors will appear here when MCP server connections or tool discoveries fail.")
        else:
            lines = []
            for server_name, errors in sorted(self.mcp_last_error.items()):
                lines.append(f"━━━ {server_name} ━━━")
                if isinstance(errors, str):
                    lines.append(f"  • {errors}")
                elif isinstance(errors, list):
                    for err_entry in reversed(errors[-5:]):  # Show most recent first
                        if isinstance(err_entry, dict):
                            err_time = err_entry.get("time", "unknown time")
                            err_msg = err_entry.get("error", "unknown error")
                            lines.append(f"  [{err_time}]")
                            lines.append(f"  • {err_msg}")
                        else:
                            lines.append(f"  • {err_entry}")
                lines.append("")
            text_area.setPlainText("\n".join(lines))
        
        layout.addWidget(text_area)
        
        btn_row = QHBoxLayout()
        
        copy_btn = QPushButton("📋 Copy to Clipboard")
        copy_btn.setStyleSheet(self._secondary_btn_style())
        copy_btn.clicked.connect(lambda: self._copy_to_clipboard(text_area.toPlainText()))
        btn_row.addWidget(copy_btn)
        
        clear_btn = QPushButton("🗑️ Clear All Errors")
        clear_btn.setStyleSheet(self._secondary_btn_style())
        clear_btn.clicked.connect(lambda: self._clear_all_errors(text_area))
        btn_row.addWidget(clear_btn)
        
        btn_row.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(self._primary_btn_style())
        close_btn.clicked.connect(dialog.accept)
        btn_row.addWidget(close_btn)
        
        layout.addLayout(btn_row)
        dialog.exec()

    def _copy_to_clipboard(self, text: str):
        """Copy text to clipboard."""
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        QMessageBox.information(self, "Copied", "Error log copied to clipboard.")

    def _clear_all_errors(self, text_area: QPlainTextEdit):
        """Clear all error history."""
        reply = QMessageBox.question(
            self,
            "Clear Errors",
            "Clear all recorded errors?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.mcp_last_error.clear()
            self._save_error_history()
            text_area.setPlainText("✅ Error log cleared.")

    def _test_one_server(self, item: QTreeWidgetItem):
        """Run discovery for the server in this row; show OK (N tools) or error. Store last error for tooltip."""
        data_str = item.data(0, 32)
        if not data_str:
            return
        try:
            server_data = json.loads(data_str)
        except json.JSONDecodeError:
            return
        name = server_data.get("name", "")
        if not name:
            QMessageBox.warning(self, "Test", "No server name.")
            return
        from grizzyclaw.utils.async_runner import run_async
        try:
            tools, err = run_async(discover_one_server(self.mcp_file, name))
        except Exception as e:
            err = str(e)
            tools = []
        if err:
            self._record_error(name, err)
            QMessageBox.warning(self, f"Test: {name}", err)
        else:
            self._clear_error(name)
            QMessageBox.information(self, f"Test: {name}", f"OK — {len(tools)} tools")
        # Update tools count label for this row
        tools_lbl = self._mcp_cell_widget(item, 2)
        if tools_lbl:
            tools_lbl.setText(str(len(tools)) if tools else "0")
        self.refresh_mcp_statuses()

    def discover_mcp_network(self):
        """Discover MCP servers on the local network via ZeroConf/mDNS; add selected one."""
        class DiscoverWorker(QThread):
            finished = pyqtSignal(list)

            def run(self):
                result = discover_mcp_servers_zeroconf(timeout_seconds=5.0)
                self.finished.emit(result)

        self._discover_worker = DiscoverWorker(self)
        self._discover_worker.finished.connect(self._on_discover_finished)
        self._discover_worker.start()
        QMessageBox.information(
            self,
            "Discovering",
            "Searching for MCP servers on the local network (_mcp._tcp.local.)…\nThis may take a few seconds.",
        )

    def _on_discover_finished(self, servers: list):
        if not servers:
            QMessageBox.information(
                self,
                "Network discovery",
                "No MCP servers found. Servers must advertise _mcp._tcp.local. (ZeroConf).\nInstall the 'zeroconf' package if needed.",
            )
            return
        items = [f"{s.get('name', '?')} — {s.get('host', '')}:{s.get('port', 0)}" for s in servers]
        choice, ok = QInputDialog.getItem(
            self,
            "Add discovered server",
            "Select an MCP server to add (HTTP):",
            items,
            0,
            False,
        )
        if ok and choice:
            idx = items.index(choice)
            s = servers[idx]
            name = (s.get("name") or "discovered").replace(" ", "_")[:64]
            host = s.get("host", "localhost")
            port = s.get("port", 0)
            url = f"http://{host}:{port}"
            config = {"name": name, "url": url}
            self.mcp_servers_data.append(config)
            self._save_mcp_data()
            self.load_mcp_list()
            QMessageBox.information(self, "Added", f"Added '{name}' with URL {url}")

    def add_mcp_from_marketplace(self):
        """Open the enhanced marketplace dialog for browsing and installing MCP servers."""
        marketplace_url = getattr(self.settings, "mcp_marketplace_url", None) or self.mcp_marketplace_url.text().strip() or None
        existing_names = {s.get("name", "").strip().lower() for s in self.mcp_servers_data}
        dialog = MarketplaceDialog(
            parent=self,
            marketplace_url=marketplace_url,
            existing_names=existing_names,
            is_dark=self.is_dark,
            mcp_file=self.mcp_file,
        )
        if dialog.exec():
            installed = dialog.get_installed_servers()
            if installed:
                invalidate_tools_cache(self.mcp_file)
                self.mcp_servers_data = self._load_mcp_data()
                self.load_mcp_list()
                # Auto-discover tools for newly installed servers
                for name in installed:
                    self._start_tools_count_worker(name)

    def _on_marketplace_selected(self, index: int):
        """Handle marketplace source selection from dropdown."""
        if index <= 0:  # Header item "Add from Marketplace..."
            return
        
        combo = self.marketplace_combo
        text = combo.currentText().strip()
        source = combo.currentData()
        
        # Reset to header after selection
        combo.blockSignals(True)
        combo.setCurrentIndex(0)
        combo.blockSignals(False)
        
        # Skip separator items (start with "──")
        if text.startswith("──"):
            return
        
        # Handle custom URL
        if source == "custom":
            url, ok = QInputDialog.getText(
                self, "Custom Marketplace URL",
                "Enter the URL of an MCP server list (JSON format):",
                text=self.mcp_marketplace_url.text().strip()
            )
            if ok and url.strip():
                self._open_marketplace_with_source(url.strip(), "Custom")
            return
        
        # Handle built-in marketplace (None source)
        if source is None:
            self.add_mcp_from_marketplace()
            return
        
        # Handle known sources
        self._open_marketplace_with_source(source, text.replace("  ", ""))

    def _open_marketplace_with_source(self, source_url: str, source_name: str):
        """Open the marketplace dialog with a specific source."""
        existing_names = {s.get("name", "").strip().lower() for s in self.mcp_servers_data}
        dialog = MarketplaceDialog(
            parent=self,
            marketplace_url=source_url,
            source_name=source_name,
            existing_names=existing_names,
            is_dark=self.is_dark,
            mcp_file=self.mcp_file,
        )
        if dialog.exec():
            installed = dialog.get_installed_servers()
            if installed:
                invalidate_tools_cache(self.mcp_file)
                self.mcp_servers_data = self._load_mcp_data()
                self.load_mcp_list()
                for name in installed:
                    self._start_tools_count_worker(name)

    def refresh_mcp_statuses(self):
        """Refresh status and fetch live tool counts asynchronously for all MCP servers."""
        for row in range(self.mcp_servers_tree.topLevelItemCount()):
            item = self.mcp_servers_tree.topLevelItem(row)
            data_str = item.data(0, 32)
            if not data_str:
                continue
            try:
                server_data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            name = server_data.get('name', '')

            # Update tools count label (set spinner while fetching)
            tools_lbl = self._mcp_cell_widget(item, 2)
            if tools_lbl:
                current = tools_lbl.text().strip()
                if current in {"--", "0"}:
                    tools_lbl.setText("…")
                # Kick off background discovery if not already running for this server
                if name and name not in self._tools_workers:
                    self._start_tools_count_worker(name)
            
            # Check status
            is_remote = bool(server_data.get('url'))
            if is_remote:
                status_icon = self._test_remote_connection(server_data)
            else:
                status_icon = self._is_local_running(server_data)
            
            is_running = status_icon == "✓"
            
            # Update button appearance
            btn = self._mcp_cell_widget(item, 1)
            if btn:
                if is_running:
                    btn.setText("🟢")
                    btn.setToolTip(f"{name} is running. Click to stop.")
                    btn.setStyleSheet(self._running_btn_style())
                else:
                    btn.setText("🔴")
                    last_err = self._get_last_error(name)
                    tip = f"{name} is stopped. Click to start."
                    if last_err:
                        tip += f"\n⚠️ Last error: {last_err[:200]}{'…' if len(last_err) > 200 else ''}"
                    btn.setToolTip(tip)
                    btn.setStyleSheet(self._stopped_btn_style())
                # Force repaint
                btn.update()
                btn.repaint()

    def _start_tools_count_worker(self, server_name: str):
        class ToolsCountWorker(QThread):
            finished_signal = pyqtSignal(str, int, str)  # name, count, err

            def __init__(self, mcp_file: Path, name: str):
                super().__init__()
                self.mcp_file = mcp_file
                self.name = name

            def run(self):
                try:
                    import asyncio
                    tools, err = asyncio.run(discover_one_server(self.mcp_file, self.name))
                    cnt = len(tools) if tools else 0
                    self.finished_signal.emit(self.name, cnt, err or "")
                except Exception as e:
                    self.finished_signal.emit(self.name, 0, str(e))

        w = ToolsCountWorker(self.mcp_file, server_name)
        self._tools_workers[server_name] = w
        w.finished_signal.connect(self._on_tools_count_finished)
        w.start()

    def _on_tools_count_finished(self, name: str, count: int, err: str):
        # Save error for tooltip and update label
        if err:
            self._record_error(name, err)
        else:
            self._clear_error(name)
        # Update matching row
        for row in range(self.mcp_servers_tree.topLevelItemCount()):
            item = self.mcp_servers_tree.topLevelItem(row)
            data_str = item.data(0, 32)
            if not data_str:
                continue
            try:
                server_data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            if server_data.get("name") == name:
                lbl = self._mcp_cell_widget(item, 2)
                if lbl:
                    lbl.setText(str(count))
                break
        # cleanup worker entry
        w = self._tools_workers.pop(name, None)
        if w:
            try:
                w.finished_signal.disconnect(self._on_tools_count_finished)
            except Exception:
                pass

    def _update_button_for_server(self, server_name, is_running):
        """Update button state for a specific server immediately."""
        for row in range(self.mcp_servers_tree.topLevelItemCount()):
            item = self.mcp_servers_tree.topLevelItem(row)
            data_str = item.data(0, 32)
            if not data_str:
                continue
            try:
                server_data = json.loads(data_str)
                if server_data.get('name') == server_name:
                    btn = self._mcp_cell_widget(item, 1)
                    if btn:
                        if is_running:
                            btn.setText("🟢")
                            btn.setToolTip(f"{server_name} is running. Click to stop.")
                            btn.setStyleSheet(self._running_btn_style())
                        else:
                            btn.setText("🔴")
                            btn.setToolTip(
                                f"{server_name} is stopped. Click to start (optional — the agent starts it automatically when using tools in chat)."
                            )
                            btn.setStyleSheet(self._stopped_btn_style())
                        btn.update()
                        btn.repaint()
                    break
            except json.JSONDecodeError:
                continue

    def _get_server_match_patterns(self, server_data):
        """Return patterns used to find this server in ps/pgrep. Shared by status
        check and stop logic so both agree. Patterns must be >= 10 chars to avoid
        false matches.
        """
        cmd = server_data.get('command', '')
        args = normalize_mcp_args(server_data.get('args', []))
        cmd_match = f"{cmd} {' '.join(map(str, args[:3]))}".strip()
        patterns = [cmd_match]
        if cmd == 'npx' and args:
            # Package name: first arg if it's not -y, else second (e.g. npx mcp-macos or npx -y mcp-macos)
            pkg = str(args[1]) if (len(args) >= 2 and args[0] == '-y') else str(args[0])
            if len(pkg) >= 10:
                patterns.append(pkg)  # match node .../mcp-macos/... after npx exits
            if len(args) >= 2 and args[0] == '-y':
                patterns.append(f"npm exec {pkg}")
                if pkg.startswith('@'):
                    patterns.append(pkg.split('/')[-1])
        elif cmd == 'uvx' and args:
            patterns.append(str(args[0]))
        elif cmd == 'node' and args:
            path = str(args[0])
            patterns.append(os.path.basename(path))
        return [p for p in patterns if len(p) >= 10]

    def _check_server_running_by_ps(self, server_data):
        """Check if local MCP server appears in ps. Uses same patterns as stop logic."""
        patterns = self._get_server_match_patterns(server_data)
        if not patterns:
            return False
        try:
            result = subprocess.run(['ps', 'aux'], capture_output=True, text=True, timeout=5)
            for line in result.stdout.splitlines():
                for pat in patterns:
                    if pat in line:
                        return True
            return False
        except Exception:
            return False

    def _is_local_running(self, server_data):
        """Check if local MCP server is running. Must match Test All exactly."""
        name = server_data.get('name')
        if not name or not server_data.get('command'):
            return "✗"

        # 1. Tracked process (we started it this session)
        proc = self.running_processes.get(name)
        if proc is not None:
            if proc.poll() is None:
                return "✓"
            self.running_processes.pop(name, None)

        # 2. Same check as Test All - line by line in ps output
        return "✓" if self._check_server_running_by_ps(server_data) else "✗"

    def _test_remote_connection(self, server_data):
        """Check if remote MCP server is reachable. Uses Python urllib (no curl) so it works in bundled app."""
        url = (server_data.get('url') or '').strip().rstrip('/') or ''
        if not url:
            return "✗"
        if not url.endswith('/'):
            url = url + '/'
        headers = server_data.get('headers') or {}
        if not isinstance(headers, dict):
            headers = {}
        try:
            req = Request(url, headers=headers)
            with urlopen(req, timeout=3) as resp:
                code = getattr(resp, 'status', 200)
                if 200 <= code < 300:
                    return "✓"
                return "✗"
        except HTTPError as e:
            # Server responded; 2xx = up, else down (e.g. 404/500)
            return "✓" if 200 <= e.code < 300 else "✗"
        except (URLError, OSError, TimeoutError):
            return "✗"
        except Exception:
            return "✗"

    def _get_expanded_env(self):
        """Get environment with expanded PATH for CLI tools.
        
        macOS GUI apps don't inherit shell environment (.zshrc/.bashrc),
        so we need to manually add common paths where tools like npx, uvx, 
        node, etc. are typically installed.
        """
        env = os.environ.copy()
        current_path = env.get('PATH', '')
        
        # Common paths for Homebrew, npm, Python tools, etc.
        extra_paths = [
            '/opt/homebrew/bin',           # Homebrew on Apple Silicon
            '/opt/homebrew/sbin',
            '/usr/local/bin',              # Homebrew on Intel / general
            '/usr/local/sbin',
            str(Path.home() / '.local' / 'bin'),  # pipx, uv installs
            str(Path.home() / '.cargo' / 'bin'),  # Rust tools
            '/usr/local/opt/node/bin',     # Node from Homebrew
            '/opt/homebrew/opt/node/bin',
            str(Path.home() / '.nvm' / 'versions' / 'node' / 'current' / 'bin'),  # nvm
            str(Path.home() / '.volta' / 'bin'),  # Volta
            '/usr/bin',
            '/bin',
        ]
        
        # Add extra paths that exist and aren't already in PATH
        for p in extra_paths:
            if os.path.isdir(p) and p not in current_path:
                current_path = f"{p}:{current_path}"
        
        env['PATH'] = current_path
        return env

    def toggle_mcp_connection(self, server_data):
        is_remote = bool(server_data.get('url'))
        name = server_data.get('name', 'Unknown')
        if is_remote:
            status = self._test_remote_connection(server_data)
            QMessageBox.information(self, name, f"Connection: {status}")
            self.refresh_mcp_statuses()
            return
        # local
        status = self._is_local_running(server_data)
        if status == "✓":
            # disconnect/stop the server
            proc = self.running_processes.pop(name, None)
            if proc is not None:
                try:
                    if proc.poll() is None:
                        try:
                            pgid = os.getpgid(proc.pid)
                            os.killpg(pgid, signal.SIGTERM)
                        except (ProcessLookupError, OSError):
                            proc.terminate()
                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                    QMessageBox.information(self, "Stopped", f"Stopped {name}")
                except ProcessLookupError:
                    QMessageBox.information(self, "Already Stopped", f"{name} already gone")
                except Exception as e:
                    QMessageBox.warning(self, "Stop Error", f"Failed to stop {name}: {str(e)}")
            else:
                # Try to find and kill by patterns (same as status check - handles npm exec, etc.)
                patterns = self._get_server_match_patterns(server_data)
                if patterns:
                    try:
                        seen = set()
                        my_pid = str(os.getpid())
                        for pat in patterns:
                            result = subprocess.run(
                                ['pgrep', '-f', pat], capture_output=True, text=True, timeout=5
                            )
                            for pid_str in (result.stdout or '').strip().split():
                                if pid_str and pid_str.isdigit() and pid_str != my_pid and pid_str not in seen:
                                    seen.add(pid_str)
                        if seen:
                            for pid_str in seen:
                                try:
                                    subprocess.run(['kill', '-TERM', pid_str], check=False)
                                except Exception:
                                    pass
                            QMessageBox.information(
                                self, "Stopped", f"Stopped {name} (killed {len(seen)} process(es))"
                            )
                        else:
                            QMessageBox.information(
                                self, "No Process", f"No running process found for {name}"
                            )
                    except Exception as e:
                        QMessageBox.warning(self, "Stop Error", f"Failed to stop {name}: {str(e)}")
                else:
                    QMessageBox.information(self, "No Process", f"No tracked process for {name}")
        else:
            # connect/start the server
            cmd = server_data.get('command')
            if not cmd:
                QMessageBox.warning(self, "Start Error", f"No command defined for {name}")
                self.refresh_mcp_statuses()
                return
            cmd_list = [cmd] + normalize_mcp_args(server_data.get("args", []))
            try:
                import time
                DEVNULL = subprocess.DEVNULL
                # Use expanded environment + any server-specific env vars
                expanded_env = self._get_expanded_env()
                for k, v in (server_data.get("env") or {}).items():
                    expanded_env[str(k)] = str(v)
                # stdin=PIPE keeps the pipe open so stdio-based MCP servers don't get EOF and exit.
                # stderr=PIPE so we can show crash output (e.g. "playwright install chromium").
                p = subprocess.Popen(
                    cmd_list,
                    stdin=subprocess.PIPE,
                    stdout=DEVNULL,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                    env=expanded_env,
                )
                self.running_processes[name] = p
                # Mark as recently started for grace period
                self.recently_started[name] = time.time()
                QMessageBox.information(
                    self,
                    "Started",
                    f"{name} started (PID: {p.pid}).\n\n"
                    "Tip: The agent also starts this server automatically when using its tools in chat. "
                    "Keeping it running here is optional (e.g. for Test)."
                )
                # IMMEDIATELY update this specific button to green
                self._update_button_for_server(name, True)
                # Save the started state to persist across app restarts
                self._save_started_servers()
                # Check shortly if the process exited (e.g. Playwright not installed); show stderr to user
                QTimer.singleShot(2000, lambda: self._check_started_server_still_running(name))
            except FileNotFoundError:
                QMessageBox.warning(self, "Start Error", f"Command not found: {cmd}\n\nMake sure {cmd} is installed and in your PATH.\n\nCommon install locations checked:\n- /opt/homebrew/bin\n- /usr/local/bin\n- ~/.local/bin")
                self._update_button_for_server(name, False)
            except Exception as e:
                QMessageBox.warning(self, "Start Error", f"Failed to start {name}:\n{str(e)}")
                self._update_button_for_server(name, False)
            return  # Don't do delayed refresh for starts - button is already green
        # Only do delayed refresh for stops
        QTimer.singleShot(500, self.refresh_mcp_statuses)
        # Save state after stopping
        self._save_started_servers()

    def _get_server_data_by_name(self, name: str) -> Optional[Dict]:
        """Return server config dict for the given name from the tree, or None."""
        for row in range(self.mcp_servers_tree.topLevelItemCount()):
            item = self.mcp_servers_tree.topLevelItem(row)
            data_str = item.data(0, 32)
            if not data_str:
                continue
            try:
                server_data = json.loads(data_str)
                if server_data.get("name") == name:
                    return server_data
            except json.JSONDecodeError:
                continue
        return None

    def _check_started_server_still_running(self, name: str):
        """If we started this server and it has already exited, show stderr so user can fix (e.g. playwright install).
        For servers like macos-mcp (npx mcp-macos), npx may exit while the real node process keeps running; if the
        server still appears in ps, treat it as running and do not show the 'exited' warning.
        """
        proc = self.running_processes.get(name)
        if proc is None:
            return
        if proc.poll() is None:
            return  # Still running
        self.running_processes.pop(name, None)
        # Check if the server is still running as a child (e.g. npx exited but node is running)
        server_data = self._get_server_data_by_name(name)
        if server_data and self._check_server_running_by_ps(server_data):
            self._save_started_servers()
            return  # Child process still running; keep button green, no warning
        self._update_button_for_server(name, False)
        self._save_started_servers()
        err_text = ""
        try:
            if proc.stderr:
                err_text = (proc.stderr.read() or b"").decode("utf-8", errors="replace").strip()
        except Exception:
            pass
        if not err_text:
            err_text = f"Process exited with code {proc.returncode}."
        hint = ""
        if "playwright" in name.lower() or "browser" in err_text.lower() or "executable" in err_text.lower():
            hint = "\n\nTo fix: run in a terminal:\n  playwright install chromium"
        QMessageBox.warning(
            self,
            f"{name} exited",
            f"{name} stopped shortly after start. This often means a dependency is missing.\n\n{err_text[:800]}{hint}",
        )
        QTimer.singleShot(300, self.refresh_mcp_statuses)

    def get_settings(self):
        skills = [self.skills_list.item(i).text() for i in range(self.skills_list.count())]
        return {
            "hf_token": self.hf_token.text() or None,
            "enabled_skills": skills,
            "mcp_marketplace_url": self.mcp_marketplace_url.text().strip() or None,
        }

    def _load_mcp_data(self):
        if not hasattr(self, 'mcp_file') or not self.mcp_file.exists():
            return []
        try:
            with open(self.mcp_file, 'r') as f:
                data = json.load(f)
            mcp_servers_obj = data.get("mcpServers", {})
            servers_list = [
                {"name": name, **cfg}
                for name, cfg in mcp_servers_obj.items()
            ]
            return servers_list
        except Exception:
            return []

    def _save_mcp_data(self):
        try:
            self.mcp_file.parent.mkdir(parents=True, exist_ok=True)
            # Persist url/headers (remote) and command/args (local) so remote servers survive save/reload
            mcp_dict = {}
            for s in self.mcp_servers_data:
                name = s.get("name")
                if not name:
                    continue
                cfg = {}
                if "url" in s:
                    cfg["url"] = s["url"]
                    if s.get("headers"):
                        cfg["headers"] = s["headers"]
                if "command" in s:
                    cfg["command"] = s["command"]
                    cfg["args"] = s.get("args", [])
                    if s.get("env"):
                        cfg["env"] = s["env"]
                    # Optional defaults
                    if isinstance(s.get("timeout_s"), int) and s.get("timeout_s") > 0:
                        cfg["timeout_s"] = s.get("timeout_s")
                    if isinstance(s.get("max_concurrency"), int) and s.get("max_concurrency") > 0:
                        cfg["max_concurrency"] = s.get("max_concurrency")
                mcp_dict[name] = cfg
            with open(self.mcp_file, 'w') as f:
                json.dump({"mcpServers": mcp_dict}, f, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", f"Could not save MCP config: {str(e)}")

    def _load_started_servers(self):
        """Load list of servers that were started in previous session.
        Do NOT add to recently_started - that causes false green. Verify via process check only.
        """
        pass  # File is used by _save_started_servers; auto-start happens in _auto_start_saved_servers when tab is shown

    def _start_server_silently(self, server_data: dict) -> bool:
        """Start an MCP server without showing the 'Started' dialog. Returns True if started."""
        name = server_data.get("name", "")
        if not name or not server_data.get("command"):
            return False
        cmd_list = [server_data["command"]] + normalize_mcp_args(server_data.get("args", []))
        try:
            import time
            expanded_env = self._get_expanded_env()
            for k, v in (server_data.get("env") or {}).items():
                expanded_env[str(k)] = str(v)
            p = subprocess.Popen(
                cmd_list,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env=expanded_env,
            )
            self.running_processes[name] = p
            self.recently_started[name] = time.time()
            self._update_button_for_server(name, True)
            self._save_started_servers()
            QTimer.singleShot(2000, lambda: self._check_started_server_still_running(name))
            return True
        except Exception:
            return False

    def _auto_start_saved_servers(self):
        """If we have a saved list of servers that were running, start them once per app launch."""
        if getattr(self, "_auto_start_done", False):
            return
        self._auto_start_done = True
        if not self.started_servers_file.exists():
            return
        try:
            data = json.loads(self.started_servers_file.read_text())
            started_names = data.get("started", [])
        except Exception:
            return
        for name in started_names:
            if not name:
                continue
            server_data = self._get_server_data_by_name(name)
            if not server_data or server_data.get("url"):
                continue  # remote or not in list
            if self._is_local_running(server_data) == "✓":
                continue
            self._start_server_silently(server_data)
    
    def _save_started_servers(self):
        """Save list of currently started servers to persist across restarts."""
        try:
            self.started_servers_file.parent.mkdir(parents=True, exist_ok=True)
            # Get list of servers that are currently running (green buttons)
            started = []
            for row in range(self.mcp_servers_tree.topLevelItemCount()):
                item = self.mcp_servers_tree.topLevelItem(row)
                btn = self._mcp_cell_widget(item, 1)
                if btn and btn.text() == '🟢':
                    data_str = item.data(0, 32)
                    if data_str:
                        try:
                            server_data = json.loads(data_str)
                            name = server_data.get('name', '')
                            if name:
                                started.append(name)
                        except json.JSONDecodeError:
                            pass
            # Also include servers we know we started this session
            for name in self.running_processes.keys():
                if name not in started:
                    started.append(name)
            
            with open(self.started_servers_file, 'w') as f:
                json.dump({'started': started}, f, indent=2)
        except Exception:
            pass  # Ignore errors saving state file

    def get_settings(self):
        return {"mcp_marketplace_url": self.mcp_marketplace_url.text().strip() or None}


class ToolDiscoveryWorker(QThread):
    """Background worker to discover tools from a newly installed MCP server."""
    finished_signal = pyqtSignal(str, int, str)  # server_name, tool_count, error
    
    def __init__(self, mcp_file: Path, server_name: str):
        super().__init__()
        self.mcp_file = mcp_file
        self.server_name = server_name
    
    def run(self):
        try:
            import asyncio
            tools, err = asyncio.run(discover_one_server(self.mcp_file, self.server_name))
            count = len(tools) if tools else 0
            self.finished_signal.emit(self.server_name, count, err or "")
        except Exception as e:
            self.finished_signal.emit(self.server_name, 0, str(e))


class ZeroConfDiscoveryWorker(QThread):
    """Background worker to discover MCP servers on the local network via ZeroConf."""
    finished_signal = pyqtSignal(list)  # list of discovered servers
    
    def __init__(self, timeout: float = 5.0):
        super().__init__()
        self.timeout = timeout
    
    def run(self):
        try:
            from grizzyclaw.mcp_client import discover_mcp_servers_zeroconf
            servers = discover_mcp_servers_zeroconf(timeout_seconds=self.timeout)
            self.finished_signal.emit(servers or [])
        except Exception:
            self.finished_signal.emit([])


class MarketplaceDialog(QDialog):
    """Enhanced MCP Server Marketplace with categories, search, featured servers, network discovery, and auto-validation."""
    
    # Server categories for better organization
    CATEGORIES = {
        "🌐 Web & Search": ["search", "ddg", "web", "browser", "playwright", "puppeteer", "scrape", "fetch"],
        "📁 Files & Storage": ["filesystem", "file", "storage", "s3", "drive", "dropbox"],
        "🔧 Development": ["github", "gitlab", "git", "docker", "kubernetes", "npm", "code", "shell"],
        "📊 Data & Analytics": ["database", "sql", "postgres", "mongo", "redis", "analytics", "sqlite"],
        "🤖 AI & ML": ["openai", "anthropic", "huggingface", "llm", "ai", "model", "memory", "thinking"],
        "📝 Productivity": ["notion", "slack", "discord", "email", "calendar", "todo", "time", "maps"],
        "🔌 Other": [],  # Default category
    }
    
    # Featured/recommended servers (by name)
    FEATURED_SERVERS = {
        "playwright-mcp", "filesystem", "github", "ddg-search", "memory", "sequential-thinking", "fetch"
    }
    
    # Estimated tool counts for popular servers (updated from real data)
    ESTIMATED_TOOLS = {
        "playwright-mcp": 8, "puppeteer": 6, "ddg-search": 3, "brave-search": 2, "fetch": 2,
        "filesystem": 11, "google-drive": 5, "github": 12, "gitlab": 10,
        "sequential-thinking": 1, "sqlite": 6, "postgres": 5, "slack": 8,
        "google-maps": 4, "memory": 3, "everart": 2, "time": 2, "shell": 1,
    }
    
    def __init__(self, parent=None, marketplace_url=None, source_name=None, existing_names=None, is_dark=False, mcp_file=None):
        super().__init__(parent)
        self.marketplace_url = marketplace_url
        self.source_name = source_name or "Marketplace"
        self.existing_names = existing_names or set()
        self.is_dark = is_dark
        self.mcp_file = mcp_file
        self.servers = []
        self.filtered_servers = []
        self.installed_servers = []
        self.network_servers = []  # Servers discovered via ZeroConf
        self.validation_workers = {}  # Track running validation workers
        self.zeroconf_worker = None
        self.sort_by = "featured"  # featured, name, category, tools
        title = f"MCP Server Marketplace - {self.source_name}" if self.source_name != "Marketplace" else "MCP Server Marketplace"
        self.setWindowTitle(title)
        self.setMinimumSize(800, 600)
        self._setup_colors()
        self._setup_ui()
        self._load_servers()
    
    def _setup_colors(self):
        if self.is_dark:
            self.bg_color = "#1E1E1E"
            self.fg_color = "#FFFFFF"
            self.card_bg = "#2D2D2D"
            self.border_color = "#3A3A3C"
            self.accent_color = "#0A84FF"
            self.warning_bg = "#3D2A1A"
            self.warning_border = "#FF9500"
            self.success_bg = "#1A3D2A"
            self.secondary_text = "#8E8E93"
        else:
            self.bg_color = "#FFFFFF"
            self.fg_color = "#1C1C1E"
            self.card_bg = "#FAFAFA"
            self.border_color = "#E5E5EA"
            self.accent_color = "#007AFF"
            self.warning_bg = "#FFF3CD"
            self.warning_border = "#FF9500"
            self.success_bg = "#D4EDDA"
            self.secondary_text = "#8E8E93"
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        self.setStyleSheet(f"background-color: {self.bg_color}; color: {self.fg_color};")
        
        # Header
        header = QLabel("🏪 MCP Server Marketplace")
        header.setFont(QFont("-apple-system", 18, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {self.fg_color}; background: transparent;")
        layout.addWidget(header)
        
        subtitle = QLabel("Browse and install MCP servers to extend your agent's capabilities. ⭐ = Featured")
        subtitle.setStyleSheet(f"color: {self.secondary_text}; font-size: 13px; background: transparent;")
        layout.addWidget(subtitle)
        
        # Search and filter row
        filter_row = QHBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Search servers by name, description, or capability...")
        self.search_input.setFixedHeight(36)
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                padding: 0 12px;
                border: 1px solid {self.border_color};
                border-radius: 8px;
                background: {self.card_bg};
                color: {self.fg_color};
                font-size: 14px;
            }}
            QLineEdit:focus {{ border: 2px solid {self.accent_color}; }}
        """)
        self.search_input.textChanged.connect(self._filter_servers)
        filter_row.addWidget(self.search_input, 2)
        
        self.category_combo = QComboBox()
        self.category_combo.addItem("All Categories")
        self.category_combo.addItem("⭐ Featured")
        for cat in self.CATEGORIES.keys():
            self.category_combo.addItem(cat)
        self.category_combo.setFixedHeight(36)
        self.category_combo.setStyleSheet(f"""
            QComboBox {{
                padding: 0 12px;
                border: 1px solid {self.border_color};
                border-radius: 8px;
                background: {self.card_bg};
                color: {self.fg_color};
                font-size: 13px;
            }}
        """)
        self.category_combo.currentTextChanged.connect(self._filter_servers)
        filter_row.addWidget(self.category_combo, 1)
        
        # Sort dropdown
        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Sort: Featured", "Sort: Name", "Sort: Tools", "Sort: Category"])
        self.sort_combo.setFixedHeight(36)
        self.sort_combo.setFixedWidth(130)
        self.sort_combo.setStyleSheet(f"""
            QComboBox {{
                padding: 0 8px;
                border: 1px solid {self.border_color};
                border-radius: 8px;
                background: {self.card_bg};
                color: {self.fg_color};
                font-size: 12px;
            }}
        """)
        self.sort_combo.currentTextChanged.connect(self._on_sort_changed)
        filter_row.addWidget(self.sort_combo)
        
        layout.addLayout(filter_row)
        
        # Server list with tools count column
        self.server_tree = QTreeWidget()
        self.server_tree.setHeaderLabels(["Server", "Description", "Tools", "Category", "Status"])
        tree_header = self.server_tree.header()
        tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        tree_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tree_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        tree_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        tree_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.server_tree.setColumnWidth(0, 180)
        self.server_tree.setColumnWidth(2, 55)
        self.server_tree.setColumnWidth(3, 130)
        self.server_tree.setColumnWidth(4, 100)
        self.server_tree.setMinimumHeight(250)
        self.server_tree.setAlternatingRowColors(True)
        self.server_tree.setRootIsDecorated(False)
        self.server_tree.setIndentation(0)
        self.server_tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.server_tree.setStyleSheet(f"""
            QTreeWidget {{
                border: 1px solid {self.border_color};
                border-radius: 8px;
                background: {self.card_bg};
                color: {self.fg_color};
                alternate-background-color: {self.bg_color};
            }}
            QTreeWidget::item {{ padding: 8px 4px; }}
            QTreeWidget::item:selected {{ background: {self.accent_color}; color: white; }}
            QHeaderView::section {{
                background: {self.card_bg};
                color: {self.fg_color};
                border: none;
                border-bottom: 1px solid {self.border_color};
                padding: 8px;
                font-weight: 600;
            }}
        """)
        layout.addWidget(self.server_tree)
        
        # Safety warning banner (shown when npx server selected)
        self.warning_frame = QFrame()
        self.warning_frame.setStyleSheet(f"""
            QFrame {{
                background: {self.warning_bg};
                border: 1px solid {self.warning_border};
                border-radius: 8px;
                padding: 12px;
            }}
        """)
        warning_layout = QVBoxLayout(self.warning_frame)
        warning_layout.setContentsMargins(12, 8, 12, 8)
        warning_layout.setSpacing(4)
        
        warning_title = QLabel("⚠️ Security Notice")
        warning_title.setFont(QFont("-apple-system", 13, QFont.Weight.DemiBold))
        warning_title.setStyleSheet(f"color: {self.warning_border}; background: transparent;")
        warning_layout.addWidget(warning_title)
        
        self.warning_text = QLabel(
            "This server will be installed via npx and may download and execute code from npm. "
            "Only install servers from trusted sources."
        )
        self.warning_text.setWordWrap(True)
        self.warning_text.setStyleSheet(f"color: {self.fg_color}; font-size: 12px; background: transparent;")
        warning_layout.addWidget(self.warning_text)
        
        self.warning_frame.hide()
        layout.addWidget(self.warning_frame)
        
        # Server details panel
        self.details_frame = QFrame()
        self.details_frame.setStyleSheet(f"""
            QFrame {{
                background: {self.card_bg};
                border: 1px solid {self.border_color};
                border-radius: 8px;
                padding: 12px;
            }}
        """)
        details_layout = QVBoxLayout(self.details_frame)
        details_layout.setContentsMargins(12, 8, 12, 8)
        details_layout.setSpacing(4)
        
        self.details_name = QLabel("Select a server")
        self.details_name.setFont(QFont("-apple-system", 14, QFont.Weight.DemiBold))
        self.details_name.setStyleSheet(f"color: {self.fg_color}; background: transparent;")
        details_layout.addWidget(self.details_name)
        
        self.details_desc = QLabel("")
        self.details_desc.setWordWrap(True)
        self.details_desc.setStyleSheet(f"color: {self.secondary_text}; font-size: 12px; background: transparent;")
        details_layout.addWidget(self.details_desc)
        
        self.details_cmd = QLabel("")
        self.details_cmd.setStyleSheet(f"color: {self.secondary_text}; font-size: 11px; font-family: monospace; background: transparent;")
        details_layout.addWidget(self.details_cmd)
        
        layout.addWidget(self.details_frame)
        
        # Status/progress area
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {self.secondary_text}; font-size: 12px; background: transparent;")
        layout.addWidget(self.status_label)
        
        # Button row
        btn_row = QHBoxLayout()
        
        self.install_btn = QPushButton("📦 Install Selected")
        self.install_btn.setEnabled(False)
        self.install_btn.setFixedHeight(40)
        self.install_btn.setStyleSheet(f"""
            QPushButton {{
                background: {self.accent_color};
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: 600;
                padding: 0 24px;
            }}
            QPushButton:hover {{ background: #0066CC; }}
            QPushButton:disabled {{ background: {self.border_color}; color: {self.secondary_text}; }}
        """)
        self.install_btn.clicked.connect(self._install_selected)
        btn_row.addWidget(self.install_btn)
        
        # Network scan button for ZeroConf discovery
        self.scan_btn = QPushButton("📶 Scan Network")
        self.scan_btn.setFixedHeight(40)
        self.scan_btn.setToolTip("Discover MCP servers on your local network via ZeroConf/mDNS")
        self.scan_btn.setStyleSheet(f"""
            QPushButton {{
                background: {self.card_bg};
                color: {self.fg_color};
                border: 1px solid {self.border_color};
                border-radius: 8px;
                font-size: 14px;
                padding: 0 16px;
            }}
            QPushButton:hover {{ background: {self.border_color}; }}
        """)
        self.scan_btn.clicked.connect(self._scan_network)
        btn_row.addWidget(self.scan_btn)
        
        btn_row.addStretch()
        
        close_btn = QPushButton("Close")
        close_btn.setFixedHeight(40)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: {self.card_bg};
                color: {self.fg_color};
                border: 1px solid {self.border_color};
                border-radius: 8px;
                font-size: 14px;
                padding: 0 24px;
            }}
            QPushButton:hover {{ background: {self.border_color}; }}
        """)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        
        layout.addLayout(btn_row)
    
    def _load_servers(self):
        """Load servers from selected marketplace source."""
        self.status_label.setText(f"Loading from {self.source_name}...")
        servers = []
        fetch_error = None
        source_used = "built-in"
        
        if self.marketplace_url:
            try:
                # Handle different source types
                if "github.com" in self.marketplace_url or "api.github" in self.marketplace_url:
                    source_used = "GitHub (awesome-mcp-servers)"
                    servers = self._fetch_github_source()
                elif self.marketplace_url in ["mcp-awesome", "mcpservers.org", "mcplist.ai", "mcp.so", "mcpnodes.com", "agentmcp.net"]:
                    source_used = f"Web directory ({self.marketplace_url})"
                    servers = self._fetch_web_directory(self.marketplace_url)
                else:
                    # Standard JSON URL
                    source_used = f"URL ({self.marketplace_url[:50]}...)"
                    with urlopen(Request(self.marketplace_url), timeout=10, context=SSL_CONTEXT) as resp:
                        data = json.loads(resp.read().decode())
                    servers = data.get("servers", data) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            except Exception as e:
                fetch_error = str(e)
                self.status_label.setText(f"⚠️ Couldn't reach {self.source_name}, using built-in list. ({fetch_error[:50]})")
        
        fetched_count = len(servers)
        if not servers:
            from grizzyclaw.skills.executors import DEFAULT_MCP_MARKETPLACE
            servers = DEFAULT_MCP_MARKETPLACE
            source_used = "built-in (fallback)"
        
        self.servers = servers
        self._filter_servers()
        
        # Show detailed status about fetch results
        print(f"[MCP DEBUG] Final server count: {len(self.servers)}, fetched_count: {fetched_count}, fetch_error: {fetch_error}")
        status_msg = f"Loaded {len(self.servers)} servers"
        if fetch_error:
            status_msg = f"⚠️ Fetch failed ({fetch_error[:40]}), showing {len(self.servers)} built-in servers"
        elif fetched_count > 0 and self.marketplace_url:
            status_msg = f"✓ Fetched {len(self.servers)} servers from {self.source_name}"
        self.status_label.setText(status_msg)
    
    def _fetch_github_source(self) -> list:
        """Fetch servers from a GitHub repository (awesome-mcp-servers style)."""
        servers = []
        seen_names = set()
        try:
            # Fetch the README.md and parse MCP server entries
            readme_url = "https://raw.githubusercontent.com/appcypher/awesome-mcp-servers/main/README.md"
            print(f"[MCP DEBUG] Fetching from: {readme_url}")
            req = Request(readme_url, headers={"User-Agent": "GrizzyClaw-MCP-Client/1.0"})
            with urlopen(req, timeout=20, context=SSL_CONTEXT) as resp:
                content = resp.read().decode()
            print(f"[MCP DEBUG] Fetched {len(content)} bytes")
            
            import re
            # Pattern for awesome-mcp-servers format:
            # - <img.../> [Name](url)<sup>...</sup> - description
            # or: - <img.../> [Name](url) - description
            entry_pattern = re.compile(
                r'^\s*-\s+(?:<img[^>]*>\s*)?\[([^\]]+)\]\(([^)]+)\)(?:<sup>[^<]*</sup>)?\s*[-–—]\s*(.+?)$',
                re.MULTILINE
            )
            
            for match in entry_pattern.finditer(content):
                name = match.group(1).strip()
                url = match.group(2).strip()
                desc = match.group(3).strip()
                
                # Only include GitHub-hosted MCP servers
                if 'github.com' not in url.lower():
                    continue
                
                # Skip table entries, badges, and image links
                if '<div' in name or '<img' in name or name.lower() in ('license', 'build', 'status'):
                    continue
                
                # Create a unique key for deduplication
                name_key = name.lower().replace(' ', '-')
                if name_key in seen_names:
                    continue
                seen_names.add(name_key)
                
                # Extract package name from GitHub URL for install command
                repo_parts = url.rstrip('/').split('/')
                repo_name = repo_parts[-1] if repo_parts else name
                # Handle /tree/main/src/subproject URLs
                if 'tree' in repo_parts:
                    tree_idx = repo_parts.index('tree')
                    if tree_idx > 0:
                        repo_name = repo_parts[tree_idx - 1]
                
                server_entry = {
                    "name": name.replace(' ', '-').lower()[:64],
                    "description": desc[:300] if desc else f"MCP server: {name}",
                    "github_url": url,
                }
                
                # Check if it's an official MCP server (modelcontextprotocol org)
                if 'modelcontextprotocol/servers' in url:
                    # Extract subpath like 'filesystem', 'postgres', etc.
                    if '/src/' in url:
                        subpath = url.split('/src/')[-1].rstrip('/')
                        server_entry["command"] = "npx"
                        server_entry["args"] = ["-y", f"@modelcontextprotocol/server-{subpath}"]
                else:
                    # For other servers, suggest looking at their repo
                    server_entry["command"] = "npx"
                    server_entry["args"] = ["-y", repo_name]
                
                servers.append(server_entry)
            
            print(f"[MCP DEBUG] Parsed {len(servers)} servers from README")
                
        except Exception as e:
            # Log error for debugging
            import traceback
            print(f"[MCP Marketplace] GitHub fetch error: {e}")
            print(f"[MCP DEBUG] Traceback: {traceback.format_exc()}")
        return servers
    
    def _fetch_web_directory(self, source_id: str) -> list:
        """Fetch servers from web directory APIs.
        
        Note: Most web directories don't have public APIs yet.
        This method will attempt to fetch from known API endpoints,
        but most will fail and return empty, causing fallback to built-in list.
        """
        servers = []
        
        # Known API endpoints (most are speculative as these sites don't have public APIs)
        url_map = {
            "mcp-awesome": "https://mcp-awesome.com/api/servers.json",
            "mcpservers.org": "https://mcpservers.org/api/servers.json",
            "mcplist.ai": "https://www.mcplist.ai/api/servers",
            "mcp.so": "https://mcp.so/api/v1/servers",
            "mcpnodes.com": "https://mcpnodes.com/api/servers.json",
            "agentmcp.net": "https://agentmcp.net/api/servers.json",
        }
        
        api_url = url_map.get(source_id)
        if not api_url:
            return servers
        
        try:
            req = Request(api_url, headers={
                "Accept": "application/json",
                "User-Agent": "GrizzyClaw-MCP-Client/1.0"
            })
            with urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
                data = json.loads(resp.read().decode())
            
            # Handle different response formats
            if isinstance(data, list):
                servers = data
            elif isinstance(data, dict):
                # Try common keys for server lists
                for key in ['servers', 'data', 'items', 'results', 'list']:
                    if key in data and isinstance(data[key], list):
                        servers = data[key]
                        break
                        
        except HTTPError as e:
            print(f"[MCP Marketplace] {source_id} API returned HTTP {e.code}")
        except URLError as e:
            print(f"[MCP Marketplace] {source_id} connection failed: {e.reason}")
        except Exception as e:
            print(f"[MCP Marketplace] {source_id} fetch error: {e}")
        
        return servers
    
    def _categorize_server(self, server: dict) -> str:
        """Determine the category for a server based on its name and description."""
        name = (server.get("name") or "").lower()
        desc = (server.get("description") or "").lower()
        text = f"{name} {desc}"
        
        for category, keywords in self.CATEGORIES.items():
            if not keywords:  # Skip "Other" in first pass
                continue
            for keyword in keywords:
                if keyword in text:
                    return category
        return "🔌 Other"
    
    def _on_sort_changed(self, sort_text: str):
        """Handle sort dropdown change."""
        sort_map = {
            "Sort: Featured": "featured",
            "Sort: Name": "name",
            "Sort: Tools": "tools",
            "Sort: Category": "category"
        }
        self.sort_by = sort_map.get(sort_text, "featured")
        self._filter_servers()
    
    def _scan_network(self):
        """Start ZeroConf network scan for MCP servers."""
        if self.zeroconf_worker and self.zeroconf_worker.isRunning():
            return
        
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("📶 Scanning...")
        self.status_label.setText("🔍 Scanning network for MCP servers...")
        
        self.zeroconf_worker = ZeroConfDiscoveryWorker(timeout=5.0)
        self.zeroconf_worker.finished_signal.connect(self._on_network_scan_complete)
        self.zeroconf_worker.start()
    
    def _on_network_scan_complete(self, servers: list):
        """Handle network scan completion."""
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("📶 Scan Network")
        
        if servers:
            self.network_servers = servers
            # Add network-discovered servers to the list
            for server in servers:
                server["_source"] = "network"  # Mark as network-discovered
                if server not in self.servers:
                    self.servers.append(server)
            self._filter_servers()
            self.status_label.setText(f"✅ Found {len(servers)} server(s) on your network! Total: {len(self.servers)} servers.")
        else:
            self.status_label.setText("ℹ️ No MCP servers found on local network. Try the built-in marketplace list.")
    
    def _get_estimated_tools(self, server: dict) -> str:
        """Get estimated tool count for a server."""
        name = server.get("name", "")
        # Check for explicitly set tool count
        if "tools" in server:
            return str(server["tools"])
        # Check estimated tools
        count = self.ESTIMATED_TOOLS.get(name)
        if count:
            return f"~{count}"
        return "?"  # Unknown
    
    def _is_featured(self, server: dict) -> bool:
        """Check if a server is featured."""
        # Check explicit featured flag first
        if server.get("featured"):
            return True
        name = server.get("name", "")
        return name in self.FEATURED_SERVERS
    
    def _filter_servers(self):
        """Filter and sort servers based on search text, category, and sort order."""
        search_text = self.search_input.text().lower().strip()
        selected_category = self.category_combo.currentText()
        
        self.filtered_servers = []
        for server in self.servers:
            name = (server.get("name") or "").lower()
            desc = (server.get("description") or "").lower()
            category = self._categorize_server(server)
            
            # Check if already installed
            if name in self.existing_names:
                continue
            
            # Filter by search (search in name, description, and category keywords)
            if search_text:
                # Also search in category keywords for better discovery
                cat_keywords = " ".join(self.CATEGORIES.get(category, []))
                searchable = f"{name} {desc} {cat_keywords}"
                if search_text not in searchable:
                    continue
            
            # Filter by category
            if selected_category == "⭐ Featured":
                if not self._is_featured(server):
                    continue
            elif selected_category != "All Categories" and category != selected_category:
                continue
            
            self.filtered_servers.append((server, category))
        
        # Sort the results
        self._sort_servers()
        self._populate_tree()
    
    def _sort_servers(self):
        """Sort filtered servers based on current sort setting."""
        if self.sort_by == "name":
            self.filtered_servers.sort(key=lambda x: x[0].get("name", "").lower())
        elif self.sort_by == "tools":
            def tool_sort_key(item):
                name = item[0].get("name", "")
                count = self.ESTIMATED_TOOLS.get(name, 0)
                return -count  # Descending
            self.filtered_servers.sort(key=tool_sort_key)
        elif self.sort_by == "category":
            self.filtered_servers.sort(key=lambda x: x[1])
        else:  # featured (default)
            # Featured first, then by name
            def featured_sort_key(item):
                is_featured = self._is_featured(item[0])
                name = item[0].get("name", "").lower()
                return (0 if is_featured else 1, name)
            self.filtered_servers.sort(key=featured_sort_key)
    
    def _populate_tree(self):
        """Populate the tree with filtered servers."""
        self.server_tree.clear()
        
        for server, category in self.filtered_servers:
            name = server.get("name") or "?"
            desc = server.get("description") or ""
            tools_count = self._get_estimated_tools(server)
            is_featured = self._is_featured(server)
            is_network = server.get("_source") == "network"
            
            # Add featured star or network indicator to name
            display_name = name
            if is_featured:
                display_name = f"⭐ {name}"
            elif is_network:
                display_name = f"📶 {name}"
            
            item = QTreeWidgetItem([display_name, desc[:55] + ("..." if len(desc) > 55 else ""), tools_count, category, "Available"])
            item.setData(0, Qt.ItemDataRole.UserRole, server)
            # Store original name for install
            item.setData(0, Qt.ItemDataRole.UserRole + 1, name)
            self.server_tree.addTopLevelItem(item)
        
        if not self.filtered_servers:
            item = QTreeWidgetItem(["No servers found", "Try a different search or category", "", "", ""])
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.server_tree.addTopLevelItem(item)
    
    def _on_selection_changed(self):
        """Update details panel and install button when selection changes."""
        items = self.server_tree.selectedItems()
        if not items:
            self.install_btn.setEnabled(False)
            self.warning_frame.hide()
            self.details_name.setText("Select a server")
            self.details_desc.setText("")
            self.details_cmd.setText("")
            return
        
        item = items[0]
        server = item.data(0, Qt.ItemDataRole.UserRole)
        if not server:
            self.install_btn.setEnabled(False)
            return
        
        name = server.get("name") or "?"
        desc = server.get("description") or "No description available."
        cmd = server.get("command", "npx")
        args = server.get("args", [])
        
        # Show featured/network badge and tool count in details
        tools_info = self._get_estimated_tools(server)
        is_featured = self._is_featured(server)
        is_network = server.get("_source") == "network"
        
        badge = ""
        if is_featured:
            badge = "⭐ Featured | "
        elif is_network:
            badge = "📶 Network | "
        
        self.details_name.setText(f"📦 {name}")
        self.details_desc.setText(f"{badge}Tools: {tools_info} | {desc}")
        self.details_cmd.setText(f"Command: {cmd} {' '.join(str(a) for a in args)}")
        
        self.install_btn.setEnabled(True)
        
        # Show warning for npx-based servers
        if cmd == "npx" or cmd == "uvx":
            pkg_name = args[1] if len(args) > 1 and args[0] == "-y" else (args[0] if args else name)
            self.warning_text.setText(
                f"This will install and run '{pkg_name}' via {cmd}. "
                f"The package will be downloaded from {'npm' if cmd == 'npx' else 'PyPI'} and may execute code on your system. "
                "Only proceed if you trust this package."
            )
            self.warning_frame.show()
        else:
            self.warning_frame.hide()
    
    def _install_selected(self):
        """Install the selected server with confirmation."""
        items = self.server_tree.selectedItems()
        if not items:
            return
        
        item = items[0]
        server = item.data(0, Qt.ItemDataRole.UserRole)
        if not server:
            return
        
        name = server.get("name") or server.get("id") or "mcp-server"
        cmd = server.get("command", "npx")
        args = server.get("args", ["-y", name])
        
        # Confirmation dialog for npx/uvx servers
        if cmd in ("npx", "uvx"):
            pkg_name = args[1] if len(args) > 1 and args[0] == "-y" else (args[0] if args else name)
            reply = QMessageBox.warning(
                self,
                "Confirm Installation",
                f"⚠️ You are about to install '{pkg_name}' via {cmd}.\n\n"
                f"This will download and run code from {'npm' if cmd == 'npx' else 'PyPI'}. "
                "Make sure you trust this package before proceeding.\n\n"
                "Do you want to continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        
        # Save to MCP config
        self.status_label.setText(f"Installing {name}...")
        
        try:
            mcp_file = self.mcp_file
            mcp_file.parent.mkdir(parents=True, exist_ok=True)
            
            data = {"mcpServers": {}}
            if mcp_file.exists():
                try:
                    with open(mcp_file, "r") as f:
                        data = json.load(f)
                    data.setdefault("mcpServers", {})
                except Exception:
                    pass
            
            data["mcpServers"][name] = {"command": cmd, "args": args}
            
            with open(mcp_file, "w") as f:
                json.dump(data, f, indent=2)
            
            self.installed_servers.append(name)
            self.existing_names.add(name.lower())
            
            # Update UI to show validating status
            item.setText(4, "⏳ Validating...")
            
            self.status_label.setText(f"✅ Installed '{name}'. Validating and discovering tools...")
            
            # Start background validation to get real tool count
            self._start_validation(name, item)
            
        except Exception as e:
            self.status_label.setText(f"❌ Failed to install: {str(e)}")
            QMessageBox.warning(self, "Installation Failed", str(e))
    
    def _start_validation(self, server_name: str, tree_item: QTreeWidgetItem):
        """Start background validation to discover tools for newly installed server."""
        if server_name in self.validation_workers:
            return  # Already validating
        
        worker = ToolDiscoveryWorker(self.mcp_file, server_name)
        worker.tree_item = tree_item  # Store reference for callback
        worker.finished_signal.connect(self._on_validation_complete)
        self.validation_workers[server_name] = worker
        worker.start()
    
    def _on_validation_complete(self, server_name: str, tool_count: int, error: str):
        """Handle validation completion - update UI with real tool count."""
        worker = self.validation_workers.pop(server_name, None)
        if not worker:
            return
        
        tree_item = getattr(worker, 'tree_item', None)
        
        if error:
            # Validation failed - still installed but show warning
            if tree_item:
                tree_item.setText(2, "?")  # Tools column
                tree_item.setText(4, "⚠️ Check config")  # Status column
            self.status_label.setText(f"⚠️ '{server_name}' installed but validation failed: {error[:80]}...")
        else:
            # Success - show real tool count
            if tree_item:
                tree_item.setText(2, str(tool_count))  # Update tools column with real count
                tree_item.setText(4, f"✅ {tool_count} tools")  # Status column
            self.status_label.setText(f"✅ '{server_name}' installed with {tool_count} tools available!")
            # Update estimated tools cache for future display
            self.ESTIMATED_TOOLS[server_name] = tool_count
        
        # Disconnect signal to avoid memory leaks
        try:
            worker.finished_signal.disconnect(self._on_validation_complete)
        except Exception:
            pass
        
        # Refresh the list after a short delay to remove the installed server
        QTimer.singleShot(1500, self._filter_servers)
    
    def get_installed_servers(self) -> List[str]:
        """Return list of servers that were installed during this session."""
        return self.installed_servers


class MCPDialog(QDialog):
    def __init__(self, parent=None, edit_data=None):
        super().__init__(parent)
        self.setWindowTitle("Edit MCP Server" if edit_data else "Add MCP Server")
        self.setFixedSize(560, 520)
        self.setup_ui()
        if edit_data:
            self.name_edit.setText(edit_data.get("name", ""))
            if "url" in edit_data:
                self.remote_cb.setChecked(True)
                self.url_edit.setText(edit_data.get("url", ""))
                headers_json = json.dumps(edit_data.get("headers", {}), indent=2)
                self.headers_edit.setPlainText(headers_json)
                self.toggle_fields(True)
            else:
                self.cmd_edit.setText(edit_data.get("command", ""))
                args_text = " ".join(str(a) for a in edit_data.get("args", []))
                self.args_edit.setPlainText(args_text)
                env_data = edit_data.get("env") or {}
                self.env_edit.setPlainText(self._format_env_for_edit(env_data))
                # Prefill timeout/concurrency if present
                t = edit_data.get("timeout_s")
                if isinstance(t, int) and t > 0:
                    self.timeout_edit.setText(str(t))
                mc = edit_data.get("max_concurrency")
                if isinstance(mc, int) and mc > 0:
                    self.max_conc_edit.setText(str(mc))
                # Try to extract fast-filesystem allow paths
                try:
                    args_norm = normalize_mcp_args(edit_data.get("args", []))
                    allows = []
                    i = 0
                    while i < len(args_norm):
                        if str(args_norm[i]) == "--allow" and i + 1 < len(args_norm):
                            allows.append(str(args_norm[i+1]))
                            i += 2
                        else:
                            i += 1
                    if allows:
                        self.fs_allow_paths.setText(", ".join(allows))
                except Exception:
                    pass
                self.toggle_fields(False)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.name_edit = QLineEdit()
        self.name_edit.setFixedHeight(32)
        form.addRow("Name:", self.name_edit)

        self.remote_cb = QCheckBox("Remote MCP")
        self.remote_cb.toggled.connect(self.toggle_fields)
        form.addRow("Remote:", self.remote_cb)

        self.url_edit = QLineEdit()
        self.url_edit.setFixedHeight(32)
        self.url_edit.setPlaceholderText("https://huggingface.co/mcp")
        form.addRow("URL:", self.url_edit)

        self.headers_edit = QTextEdit()
        self.headers_edit.setMaximumHeight(80)
        self.headers_edit.setPlaceholderText('{"Authorization": "Bearer hf_your_token"}')
        form.addRow("Headers JSON:", self.headers_edit)

        self.cmd_edit = QLineEdit()
        self.cmd_edit.setFixedHeight(32)
        form.addRow("Command:", self.cmd_edit)

        self.args_edit = QTextEdit()
        self.args_edit.setMaximumHeight(120)
        self.args_edit.setPlaceholderText('Space-separated e.g. --port 8000 -m mcp_server')
        form.addRow("Arguments:", self.args_edit)

        self.env_edit = QPlainTextEdit()
        self.env_edit.setPlaceholderText("KEY=value (one per line)")
        self.env_edit.setMaximumBlockCount(50)
        self.env_edit.setFixedHeight(80)
        self.env_edit.setTabChangesFocus(True)
        form.addRow("Environment:", self.env_edit)

        # Per-server defaults (optional)
        self.timeout_edit = QLineEdit()
        self.timeout_edit.setPlaceholderText("e.g. 60 (5–300)")
        self.timeout_edit.setFixedHeight(28)
        form.addRow("Default tool timeout (s):", self.timeout_edit)

        self.max_conc_edit = QLineEdit()
        self.max_conc_edit.setPlaceholderText("optional, e.g. 2")
        self.max_conc_edit.setFixedHeight(28)
        form.addRow("Max concurrent calls:", self.max_conc_edit)

        # Fast-filesystem helper (optional; comma-separated paths)
        self.fs_allow_paths = QLineEdit()
        self.fs_allow_paths.setPlaceholderText("For fast-filesystem: /Users/you/Documents, /Volumes/Storage")
        self.fs_allow_paths.setFixedHeight(28)
        form.addRow("Filesystem allow paths:", self.fs_allow_paths)

        pick_btn = QPushButton("Pick folders…")
        pick_btn.setToolTip("Add folders to '--allow' (fast-filesystem)")
        pick_btn.clicked.connect(self._pick_fs_paths)
        form.addRow("", pick_btn)

        widget = QWidget()
        widget.setLayout(form)
        layout.addWidget(widget)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self._validate_btn = QPushButton("Validate")
        self._validate_btn.setToolTip("Test connection / list tools before saving")
        self._validate_btn.clicked.connect(self._validate_config)
        btn_layout.addWidget(self._validate_btn)
        ok_btn = QPushButton("Save")
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)
        self.toggle_fields(self.remote_cb.isChecked())

    def toggle_fields(self, checked: bool):
        self.url_edit.setVisible(checked)
        self.headers_edit.setVisible(checked)
        self.cmd_edit.setVisible(not checked)
        self.args_edit.setVisible(not checked)
        self.env_edit.setVisible(not checked)
        self.timeout_edit.setVisible(not checked)
        self.max_conc_edit.setVisible(not checked)
        self.fs_allow_paths.setVisible(not checked)

    @staticmethod
    def _format_env_for_edit(env: dict) -> str:
        if not env:
            return ""
        return "\n".join(f"{k}={v}" for k, v in sorted(env.items()))

    @staticmethod
    def _parse_env_text(text: str) -> dict:
        """Parse KEY=value lines or JSON object into a dict. Empty/invalid lines ignored."""
        text = (text or "").strip()
        if not text:
            return {}
        stripped = text.strip()
        if stripped.startswith("{"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        result = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
        return result

    def _validate_config(self):
        """Run validation for current form (local: list tools; remote: HTTP check) in a worker thread to avoid crashes."""
        if self.remote_cb.isChecked():
            url = self.url_edit.text().strip()
            if not url:
                QMessageBox.warning(self, "Validate", "Enter URL first.")
                return
            headers_text = self.headers_edit.toPlainText().strip()
            try:
                headers = json.loads(headers_text) if headers_text else {}
            except json.JSONDecodeError:
                QMessageBox.warning(self, "Validate", "Invalid headers JSON.")
                return
            config = {"url": url, "headers": headers}
        else:
            cmd = self.cmd_edit.text().strip()
            if not cmd:
                QMessageBox.warning(self, "Validate", "Enter command first.")
                return
            args_text = self.args_edit.toPlainText().strip()
            args = normalize_mcp_args(args_text) if args_text else []
            env = self._parse_env_text(self.env_edit.toPlainText())
            config = {"command": cmd, "args": args, "env": env}
        # Run in QThread so we don't block the main thread or re-raise from another thread (avoids crashes)
        self._validate_btn.setEnabled(False)
        self._validate_worker = ValidateConfigWorker(config)
        self._validate_worker.finished_signal.connect(self._on_validate_finished)
        self._validate_worker.start()

    def _on_validate_finished(self, ok: bool, msg: str):
        worker = getattr(self, "_validate_worker", None)
        if worker:
            try:
                worker.finished_signal.disconnect(self._on_validate_finished)
            except (TypeError, RuntimeError):
                pass
        self._validate_worker = None
        try:
            self._validate_btn.setEnabled(True)
        except RuntimeError:
            pass  # Dialog may already be closed
        try:
            if ok:
                QMessageBox.information(self, "Validate", msg)
            else:
                QMessageBox.warning(self, "Validate", msg)
        except RuntimeError:
            pass  # Dialog closed before validation finished

    def accept(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Error", "Name is required")
            return
        if self.remote_cb.isChecked():
            url = self.url_edit.text().strip()
            if not url:
                QMessageBox.warning(self, "Error", "URL required for remote")
                return
            headers_text = self.headers_edit.toPlainText().strip()
            try:
                json.loads(headers_text) if headers_text else {}
            except json.JSONDecodeError:
                QMessageBox.warning(self, "Error", "Invalid headers JSON")
                return
        super().accept()

    def get_config(self):
        name = self.name_edit.text().strip()
        if self.remote_cb.isChecked():
            url = self.url_edit.text().strip()
            headers_text = self.headers_edit.toPlainText().strip()
            headers = json.loads(headers_text) if headers_text else {}
            return {"name": name, "url": url, "headers": headers}
        else:
            cmd = self.cmd_edit.text().strip()
            args_text = self.args_edit.toPlainText().strip()
            args = normalize_mcp_args(args_text) if args_text else []
            # Inject fast-filesystem --allow paths if provided
            allow_text = (self.fs_allow_paths.text() or "").strip()
            if allow_text:
                paths = [p.strip() for p in allow_text.split(",") if p.strip()]
                for p in paths:
                    args += ["--allow", p]
            env = self._parse_env_text(self.env_edit.toPlainText())
            cfg = {"name": name, "command": cmd, "args": args, "env": env}
            # Optional defaults
            try:
                t = int((self.timeout_edit.text() or "0").strip() or 0)
                if t > 0:
                    cfg["timeout_s"] = max(5, min(300, t))
            except Exception:
                pass
            try:
                mc = int((self.max_conc_edit.text() or "0").strip() or 0)
                if mc > 0:
                    cfg["max_concurrency"] = max(1, min(16, mc))
            except Exception:
                pass
            return cfg

    def _pick_fs_paths(self):
        # Let user choose one folder at a time; append to list
        path = QFileDialog.getExistingDirectory(self, "Select folder to allow")
        if path:
            current = (self.fs_allow_paths.text() or "").strip()
            parts = [p.strip() for p in current.split(",") if p.strip()] if current else []
            if path not in parts:
                parts.append(path)
            self.fs_allow_paths.setText(", ".join(parts))


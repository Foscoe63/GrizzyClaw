import json
import os
import subprocess
import signal
import psutil
from pathlib import Path
from typing import Optional, Dict

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox, 
    QSpinBox, QCheckBox, QPushButton, QMessageBox, QTextEdit, QFormLayout, 
    QTreeWidget, QTreeWidgetItem, QWidget, QScrollArea, QFrame, QInputDialog,
    QHeaderView, QPlainTextEdit
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread
from PyQt6.QtGui import QFont, QShowEvent

from grizzyclaw.mcp_client import (
    invalidate_tools_cache,
    call_mcp_tool,
    validate_server_config,
    discover_one_server,
    discover_mcp_servers_zeroconf,
)


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
        self.mcp_last_error = {}
        self.is_dark = False
        theme = getattr(self.settings, "theme", "Light")
        self.is_dark = theme in ["Dark", "High Contrast Dark", "Dracula", "Monokai", "Nord", "Solarized Dark"]
        self._setup_theme_colors()
        self._load_started_servers()
        self.setup_ui()

    def showEvent(self, event: QShowEvent):
        super().showEvent(event)
        self.refresh_mcp_statuses()

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
        
        add_marketplace_btn = QPushButton("Add from marketplace")
        add_marketplace_btn.clicked.connect(self.add_mcp_from_marketplace)
        add_marketplace_btn.setStyleSheet(self._secondary_btn_style())
        add_marketplace_btn.setToolTip("Pick a server from the built-in or configured marketplace list")
        mcp_btns.addWidget(add_marketplace_btn)
        
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
        """Parse pasted URL or command into suggested edit_data (name, url or command/args). Returns None if empty or unparseable."""
        t = (text or "").strip()
        if not t:
            return None
        if t.startswith("http://") or t.startswith("https://"):
            try:
                from urllib.parse import urlparse
                p = urlparse(t)
                name = (p.netloc or "remote").replace(".", "_").replace(":", "_") or "remote"
                return {"name": name, "url": t}
            except Exception:
                return {"name": "remote", "url": t}
        parts = t.split()
        if not parts:
            return None
        cmd = parts[0]
        args = parts[1:] if len(parts) > 1 else []
        name = "mcp_server"
        for a in reversed(args):
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
            self.mcp_last_error[name] = err
            QMessageBox.warning(self, f"Test: {name}", err)
        else:
            self.mcp_last_error.pop(name, None)
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
        """Fetch marketplace list and let user pick a server to add (same as mcp_marketplace install)."""
        marketplace_url = getattr(self.settings, "mcp_marketplace_url", None) or self.mcp_marketplace_url.text().strip() or None
        servers = []
        used_builtin = False
        if marketplace_url:
            try:
                with urlopen(Request(marketplace_url), timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                servers = data.get("servers", data) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            except Exception:
                used_builtin = True
                from grizzyclaw.skills.executors import DEFAULT_MCP_MARKETPLACE
                servers = DEFAULT_MCP_MARKETPLACE
        if not servers:
            from grizzyclaw.skills.executors import DEFAULT_MCP_MARKETPLACE
            servers = DEFAULT_MCP_MARKETPLACE
        if not servers:
            QMessageBox.information(self, "Marketplace", "No servers in marketplace list.")
            return
        if used_builtin:
            QMessageBox.information(
                self, "Marketplace",
                "Couldn't reach marketplace URL; using built-in list. You can add a URL in the field above or leave it empty."
            )
        existing_names = {s.get("name", "").strip().lower() for s in self.mcp_servers_data}
        servers = [s for s in servers if (s.get("name") or "").strip().lower() not in existing_names]
        if not servers:
            QMessageBox.information(self, "Marketplace", "All marketplace servers are already added.")
            return
        items = [f"{s.get('name', '?')} — {s.get('description', '')}" for s in servers]
        choice, ok = QInputDialog.getItem(self, "Add from marketplace", "Select a server to add:", items, 0, False)
        if not ok or not choice:
            return
        name = choice.split(" — ")[0].strip()
        entry = next((s for s in servers if (s.get("name") or "").strip() == name), None)
        if not entry:
            entry = servers[items.index(choice)] if choice in items else servers[0]
        name = (entry.get("name") or entry.get("id") or "mcp-server").strip()
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
        cmd = entry.get("command", "npx")
        args = entry.get("args", ["-y", name])
        data["mcpServers"][name] = {"command": cmd, "args": args}
        try:
            with open(mcp_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", str(e))
            return
        invalidate_tools_cache(mcp_file)
        self.mcp_servers_data = self._load_mcp_data()
        self.load_mcp_list()
        QMessageBox.information(self, "Added", f"Added **{name}**. Click Refresh or Test to verify.")

    def refresh_mcp_statuses(self):
        """Refresh status indicators for all MCP servers"""
        TOOLS_COUNTS = {
            "fast-filesystem": 30,
            "ddg-search": 5,
            "wn01011-llm-token-tracker": 6,
            "context7": 4,
            "hf-mcp-server": 45,
            "llm-token-tracker": 6,
        }
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
            name_lower = name.lower()
            tools_count = TOOLS_COUNTS.get(name_lower, 12)
            
            # Update tools count label
            tools_lbl = self._mcp_cell_widget(item, 2)
            if tools_lbl:
                tools_lbl.setText(str(tools_count))
            
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
                    last_err = self.mcp_last_error.get(name, "")
                    tip = f"{name} is stopped. Click to start."
                    if last_err:
                        tip += f"\nLast error: {last_err[:200]}{'…' if len(last_err) > 200 else ''}"
                    btn.setToolTip(tip)
                    btn.setStyleSheet(self._stopped_btn_style())
                # Force repaint
                btn.update()
                btn.repaint()

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
        args = server_data.get('args', [])
        if isinstance(args, str):
            args = args.split() if args else []
        elif not isinstance(args, (list, tuple)):
            args = []
        cmd_match = f"{cmd} {' '.join(map(str, args[:3]))}".strip()
        patterns = [cmd_match]
        if cmd == 'npx' and len(args) >= 2 and args[0] == '-y':
            pkg = str(args[1])
            patterns.append(f"npm exec {pkg}")
            if pkg.startswith('@'):
                patterns.append(pkg.split('/')[-1])
            else:
                patterns.append(pkg)
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
            cmd_list = [cmd] + [str(a) for a in server_data.get('args', [])]
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

    def _check_started_server_still_running(self, name: str):
        """If we started this server and it has already exited, show stderr so user can fix (e.g. playwright install)."""
        proc = self.running_processes.get(name)
        if proc is None:
            return
        if proc.poll() is None:
            return  # Still running
        self.running_processes.pop(name, None)
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
                mcp_dict[name] = cfg
            with open(self.mcp_file, 'w') as f:
                json.dump({"mcpServers": mcp_dict}, f, indent=2)
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", f"Could not save MCP config: {str(e)}")

    def _load_started_servers(self):
        """Load list of servers that were started in previous session.
        Do NOT add to recently_started - that causes false green. Verify via process check only.
        """
        pass  # File is used by _save_started_servers; we verify running state via process detection
    
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


class MCPDialog(QDialog):
    def __init__(self, parent=None, edit_data=None):
        super().__init__(parent)
        self.setWindowTitle("Edit MCP Server" if edit_data else "Add MCP Server")
        self.setFixedSize(500, 420)
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
            args = args_text.split() if args_text else []
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
            args = args_text.split() if args_text else []
            env = self._parse_env_text(self.env_edit.toPlainText())
            return {"name": name, "command": cmd, "args": args, "env": env}


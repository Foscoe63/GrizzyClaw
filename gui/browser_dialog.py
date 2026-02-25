"""Browser automation dialog"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QMessageBox, QLineEdit, QTextEdit, QGroupBox, QFormLayout,
    QComboBox, QCheckBox, QSplitter
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QPixmap
import asyncio


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


class BrowserWorker(QThread):
    """Worker thread for browser actions to avoid blocking the GUI"""
    result_ready = pyqtSignal(str, str)  # action, result
    error_occurred = pyqtSignal(str, str)  # action, error
    
    def __init__(self, agent, action, params):
        super().__init__()
        self.agent = agent
        self.action = action
        self.params = params
    
    def run(self):
        """Execute the browser action in a separate thread"""
        try:
            async def execute():
                return await self.agent._execute_browser_action(self.action, self.params)
            
            from grizzyclaw.utils.async_runner import run_async
            result = run_async(execute())
            self.result_ready.emit(self.action, str(result))
        except Exception as e:
            self.error_occurred.emit(self.action, str(e))


class BrowserDialog(QDialog):
    def __init__(self, agent, parent=None):
        super().__init__(parent)
        self.agent = agent
        self.setWindowTitle("🌐 Browser Automation")
        self.setMinimumSize(800, 600)
        self._colors = _get_dialog_theme_colors(parent)
        self.setup_ui()
        self.check_status()

    def setup_ui(self):
        c = self._colors
        self.setStyleSheet(f"QDialog {{ background-color: {c['bg']}; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # Header
        header = QLabel("Browser Automation")
        header.setFont(QFont("-apple-system", 18, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {c['fg']};")
        layout.addWidget(header)

        # Status
        self.status_label = QLabel("Checking browser status...")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            f"font-size: 14px; padding: 10px; background: {c['summary_bg']}; "
            f"color: {c['fg']}; border-radius: 8px;"
        )
        layout.addWidget(self.status_label)

        # Navigation section
        nav_group = QGroupBox("Navigation")
        nav_layout = QFormLayout(nav_group)

        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com")
        self.url_input.returnPressed.connect(self.navigate)
        url_row.addWidget(self.url_input)

        go_btn = QPushButton("Go")
        go_btn.clicked.connect(self.navigate)
        go_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {c['accent']};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
            }}
        """)
        url_row.addWidget(go_btn)
        nav_layout.addRow("URL:", url_row)

        layout.addWidget(nav_group)

        # Actions section
        actions_group = QGroupBox("Quick Actions")
        actions_layout = QHBoxLayout(actions_group)

        screenshot_btn = QPushButton("📸 Screenshot")
        screenshot_btn.clicked.connect(self.take_screenshot)
        actions_layout.addWidget(screenshot_btn)

        self.full_page_check = QCheckBox("Full Page")
        actions_layout.addWidget(self.full_page_check)

        get_text_btn = QPushButton("📝 Get Text")
        get_text_btn.clicked.connect(self.get_page_text)
        actions_layout.addWidget(get_text_btn)

        get_links_btn = QPushButton("🔗 Get Links")
        get_links_btn.clicked.connect(self.get_page_links)
        actions_layout.addWidget(get_links_btn)

        scroll_down_btn = QPushButton("⬇️ Scroll Down")
        scroll_down_btn.clicked.connect(lambda: self.scroll("down"))
        actions_layout.addWidget(scroll_down_btn)

        scroll_up_btn = QPushButton("⬆️ Scroll Up")
        scroll_up_btn.clicked.connect(lambda: self.scroll("up"))
        actions_layout.addWidget(scroll_up_btn)

        actions_layout.addStretch()
        layout.addWidget(actions_group)

        # Custom action section
        custom_group = QGroupBox("Custom Action")
        custom_layout = QFormLayout(custom_group)

        self.action_combo = QComboBox()
        self.action_combo.addItems([
            "click",
            "fill",
            "type",
            "press_key",
            "wait_for_selector",
        ])
        custom_layout.addRow("Action:", self.action_combo)

        self.selector_input = QLineEdit()
        self.selector_input.setPlaceholderText("CSS selector (e.g., button.submit, #email)")
        custom_layout.addRow("Selector:", self.selector_input)

        self.value_input = QLineEdit()
        self.value_input.setPlaceholderText("Value (for fill/type actions)")
        custom_layout.addRow("Value:", self.value_input)

        execute_btn = QPushButton("▶️ Execute")
        execute_btn.clicked.connect(self.execute_custom)
        execute_btn.setStyleSheet("""
            QPushButton {
                background-color: #34C759;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
            }
        """)
        custom_layout.addRow("", execute_btn)

        layout.addWidget(custom_group)

        # Output section
        output_group = QGroupBox("Output")
        output_layout = QVBoxLayout(output_group)

        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        self.output_text.setStyleSheet(f"""
            QTextEdit {{
                border: 1px solid {c['border']};
                border-radius: 8px;
                padding: 8px;
                font-family: Monaco, monospace;
                font-size: 12px;
                background: {c['input_bg']};
                color: {c['fg']};
            }}
        """)
        output_layout.addWidget(self.output_text)

        clear_btn = QPushButton("Clear Output")
        clear_btn.clicked.connect(lambda: self.output_text.clear())
        output_layout.addWidget(clear_btn)

        layout.addWidget(output_group)

    def check_status(self):
        """Check if browser/playwright is available; show current URL and last action if any."""
        from grizzyclaw.automation import PLAYWRIGHT_AVAILABLE
        if PLAYWRIGHT_AVAILABLE:
            base = (
                "✅ Browser automation available. Playwright is installed.\n"
                "The browser will start automatically when you perform an action."
            )
            state = getattr(self.agent, "get_last_browser_state", lambda: {})()
            url = state.get("current_url", "").strip()
            last_action = state.get("last_action", "").strip()
            if url or last_action:
                base += "\n\n"
                if url:
                    base += f"Current URL: {url}\n"
                if last_action:
                    base += f"Last action: {last_action}"
            self.status_label.setText(base)
            self.status_label.setStyleSheet(
                "font-size: 14px; padding: 10px; background: #D4EDDA; border-radius: 8px; color: #155724;"
            )
        else:
            self.status_label.setText(
                "❌ Browser automation not available.\n"
                "Run: pip install playwright && playwright install chromium"
            )
            self.status_label.setStyleSheet(
                "font-size: 14px; padding: 10px; background: #F8D7DA; border-radius: 8px; color: #721C24;"
            )

    def log_output(self, text):
        """Add text to output"""
        self.output_text.append(text)
        self.output_text.append("")

    def run_browser_action(self, action, params):
        """Execute a browser action in background thread"""
        # Show loading indicator
        self.log_output(f"[{action}] ⏳ Running...")
        self.setEnabled(False)  # Disable dialog while running
        
        # Create and start worker thread
        self.worker = BrowserWorker(self.agent, action, params)
        self.worker.result_ready.connect(self.on_browser_result)
        self.worker.error_occurred.connect(self.on_browser_error)
        self.worker.finished.connect(lambda: self.setEnabled(True))
        self.worker.start()
    
    def on_browser_result(self, action, result):
        """Handle successful browser action result"""
        self.log_output(f"[{action}] ✅ {result}")
        self.check_status()
    
    def on_browser_error(self, action, error):
        """Handle browser action error"""
        self.log_output(f"[{action}] ❌ Error: {error}")

    def navigate(self):
        """Navigate to URL"""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "No URL", "Please enter a URL.")
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
            self.url_input.setText(url)
        self.run_browser_action("navigate", {"url": url})

    def take_screenshot(self):
        """Take a screenshot"""
        full_page = self.full_page_check.isChecked()
        self.run_browser_action("screenshot", {"full_page": full_page})

    def get_page_text(self):
        """Get page text"""
        self.run_browser_action("get_text", {"selector": "body"})

    def get_page_links(self):
        """Get all links on page"""
        self.run_browser_action("get_links", {})

    def scroll(self, direction):
        """Scroll the page"""
        self.run_browser_action("scroll", {"direction": direction, "amount": 500})

    def execute_custom(self):
        """Execute custom action"""
        action = self.action_combo.currentText()
        selector = self.selector_input.text().strip()
        value = self.value_input.text().strip()

        if not selector and action not in ["press_key"]:
            QMessageBox.warning(self, "No Selector", "Please enter a CSS selector.")
            return

        params = {}
        if action == "click":
            params = {"selector": selector}
        elif action == "fill":
            params = {"selector": selector, "value": value}
        elif action == "type":
            params = {"selector": selector, "text": value}
        elif action == "press_key":
            params = {"key": value or "Enter"}
        elif action == "wait_for_selector":
            params = {"selector": selector}

        self.run_browser_action(action, params)

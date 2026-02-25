import re
import subprocess
import sys
from pathlib import Path
import asyncio
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTabWidget, QWidget, QFormLayout, QCheckBox,
    QSpinBox, QComboBox, QGroupBox, QMessageBox, QScrollArea,
    QFrame, QFileDialog, QGridLayout, QButtonGroup,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont

from grizzyclaw.config import Settings, get_config_path
from grizzyclaw.mcp_client import invalidate_tools_cache, discover_one_server, validate_server_config


def _sanitize_telegram_token(raw: str) -> str | None:
    """Extract a valid Telegram bot token from pasted text.
    Handles copy-paste artifacts: newlines, spaces, invisible Unicode, etc.
    Token format: digits:alphanumeric (e.g. 123456789:ABCdef...)
    """
    if not raw:
        return None
    # Strip whitespace and common invisible chars (BOM, zero-width, etc.)
    cleaned = raw.strip().replace("\n", "").replace("\r", "").replace("\t", "")
    cleaned = "".join(c for c in cleaned if c.isprintable() or c in " \t")
    cleaned = cleaned.strip()
    # Extract token via regex (digits:alphanumeric+hyphen+underscore)
    match = re.search(r"\d+:[A-Za-z0-9_-]{20,}", cleaned)
    return match.group(0) if match else (cleaned if cleaned else None)


def _is_system_dark() -> bool:
    """Detect if system prefers dark theme (Qt 6.5+ colorScheme)."""
    try:
        app = QApplication.instance()
        if app and hasattr(app, "styleHints"):
            hints = app.styleHints()
            if hasattr(hints, "colorScheme"):
                return hints.colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        pass
    return False


from grizzyclaw.llm.ollama import OllamaProvider
from grizzyclaw.llm.lmstudio import LMStudioProvider, _normalize_lmstudio_url
from grizzyclaw.llm.openai import OpenAIProvider


class ModelFetchWorker(QThread):
    """Worker thread to fetch models from a provider asynchronously"""
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self.provider = provider

    def run(self):
        """Fetch models in a background thread"""
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            models = loop.run_until_complete(self.provider.list_models())
            loop.close()
            self.finished.emit(models)
        except Exception as e:
            self.error.emit(str(e))


class TelegramTestWorker(QThread):
    """Worker thread to test Telegram bot token via getMe API"""
    finished = pyqtSignal(bool, str)  # success, message

    def __init__(self, token: str, parent=None):
        super().__init__(parent)
        self.token = _sanitize_telegram_token(token or "") or ""

    def run(self):
        try:
            if not self.token:
                self.finished.emit(False, "Token is empty or invalid format. Paste the full token from @BotFather.")
                return
            from telegram import Bot
            bot = Bot(self.token)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            me = loop.run_until_complete(bot.get_me())
            loop.close()
            username = getattr(me, "username", None) or "unknown"
            self.finished.emit(True, f"Connected successfully as @{username}")
        except Exception as e:
            self.finished.emit(False, str(e))


class SettingsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)


class GeneralTab(SettingsTab):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(40, 24, 40, 24)
        container_layout.setSpacing(24)

        general_group = QGroupBox("General")
        general_group.setStyleSheet(self.get_group_style())
        form = QFormLayout(general_group)
        form.setSpacing(18)

        self.app_name = QLineEdit(self.settings.app_name)
        self.app_name.setFixedHeight(32)
        form.addRow("App Name:", self.app_name)
        self.debug_mode = QCheckBox("Enable Debug Mode")
        self.debug_mode.setChecked(self.settings.debug)
        form.addRow("", self.debug_mode)
        self.provider_combo = QComboBox()
        self.provider_combo.setEditable(False)
        self.provider_combo.addItems(["ollama", "lmstudio", "openai", "anthropic", "openrouter", "custom"])
        self.provider_combo.setCurrentText(self.settings.default_llm_provider)
        self.provider_combo.setFixedHeight(32)
        form.addRow("Default Provider:", self.provider_combo)
        model_hint = QLabel("Configure models in the 'LLM Providers' tab")
        model_hint.setStyleSheet("font-size: 12px; color: #8E8E93;")
        form.addRow("", model_hint)
        self.context_spin = QSpinBox()
        self.context_spin.setRange(1000, 100000)
        self.context_spin.setValue(self.settings.max_context_length)
        self.context_spin.setSingleStep(1000)
        self.context_spin.setFixedHeight(32)
        form.addRow("Max Context:", self.context_spin)
        self.log_combo = QComboBox()
        self.log_combo.setEditable(False)
        self.log_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.log_combo.setCurrentText(self.settings.log_level)
        self.log_combo.setFixedHeight(32)
        form.addRow("Log Level:", self.log_combo)
        self.max_agentic_iterations = QSpinBox()
        self.max_agentic_iterations.setRange(3, 30)
        self.max_agentic_iterations.setValue(getattr(self.settings, "max_agentic_iterations", 10))
        self.max_agentic_iterations.setFixedHeight(32)
        form.addRow("Max tool-use rounds:", self.max_agentic_iterations)
        self.memory_retrieval_limit_spin = QSpinBox()
        self.memory_retrieval_limit_spin.setRange(3, 50)
        self.memory_retrieval_limit_spin.setValue(getattr(self.settings, "memory_retrieval_limit", 10))
        self.memory_retrieval_limit_spin.setFixedHeight(32)
        form.addRow("Memory retrieval limit:", self.memory_retrieval_limit_spin)
        self.agent_reflection = QCheckBox("Prompt to continue or answer after tool results")
        self.agent_reflection.setChecked(getattr(self.settings, "agent_reflection_enabled", True))
        form.addRow("", self.agent_reflection)
        self.agent_plan = QCheckBox("Ask for PLAN before tools (complex tasks)")
        self.agent_plan.setChecked(getattr(self.settings, "agent_plan_before_tools", False))
        form.addRow("", self.agent_plan)
        self.agent_tool_result_max = QSpinBox()
        self.agent_tool_result_max.setRange(500, 20000)
        self.agent_tool_result_max.setValue(getattr(self.settings, "agent_tool_result_max_chars", 4000))
        self.agent_tool_result_max.setFixedHeight(32)
        form.addRow("Tool result max chars:", self.agent_tool_result_max)
        self.agent_retry_failure = QCheckBox("Retry hint when a tool fails")
        self.agent_retry_failure.setChecked(getattr(self.settings, "agent_retry_on_tool_failure", True))
        form.addRow("", self.agent_retry_failure)
        self.max_session_messages_spin = QSpinBox()
        self.max_session_messages_spin.setRange(5, 200)
        self.max_session_messages_spin.setValue(getattr(self.settings, "max_session_messages", 20))
        self.max_session_messages_spin.setFixedHeight(32)
        self.max_session_messages_spin.setToolTip("Context window: older turns are trimmed; tool-heavy turns get priority slots.")
        form.addRow("Max session messages:", self.max_session_messages_spin)

        container_layout.addWidget(general_group)
        scroll.setWidget(container)
        layout.addWidget(scroll)

    def get_group_style(self):
        dialog = self.window()
        if isinstance(dialog, SettingsDialog) and getattr(dialog, "is_dark", False):
            return "QGroupBox { font-weight: 600; font-size: 13px; border: 1px solid #3A3A3C; border-radius: 6px; margin-top: 8px; margin-bottom: 8px; padding: 8px 16px 16px 16px; background: #2C2C2E; } QGroupBox::title { subcontrol-origin: padding; left: 0; top: 0; padding-bottom: 4px; color: #FFFFFF; }"
        return "QGroupBox { font-weight: 600; font-size: 13px; border: 1px solid #E5E5EA; border-radius: 6px; margin-top: 8px; margin-bottom: 8px; padding: 8px 16px 16px 16px; background: #FAFAFA; } QGroupBox::title { subcontrol-origin: padding; left: 0; top: 0; padding-bottom: 4px; color: #1C1C1E; }"

    def get_settings(self):
        return {
            "app_name": self.app_name.text(),
            "debug": self.debug_mode.isChecked(),
            "default_llm_provider": self.provider_combo.currentText(),
            "max_context_length": self.context_spin.value(),
            "log_level": self.log_combo.currentText(),
            "max_agentic_iterations": self.max_agentic_iterations.value(),
            "memory_retrieval_limit": self.memory_retrieval_limit_spin.value(),
            "agent_reflection_enabled": self.agent_reflection.isChecked(),
            "agent_plan_before_tools": self.agent_plan.isChecked(),
            "agent_tool_result_max_chars": self.agent_tool_result_max.value(),
            "agent_retry_on_tool_failure": self.agent_retry_failure.isChecked(),
            "max_session_messages": self.max_session_messages_spin.value(),
        }


class LLMTab(SettingsTab):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(40, 20, 40, 20)
        container_layout.setSpacing(12)
        
        # Ollama
        ollama_group = self.create_group("Ollama (Local)")
        ollama_form = QFormLayout(ollama_group)
        ollama_form.setSpacing(12)

        self.ollama_url = QLineEdit(self.settings.ollama_url)
        self.ollama_url.setFixedHeight(32)
        ollama_form.addRow("URL:", self.ollama_url)

        ollama_hint = QLabel("Default: http://localhost:11434")
        ollama_hint.setStyleSheet("font-size: 12px;")
        ollama_form.addRow("", ollama_hint)

        # Model selection with refresh button
        model_layout = QHBoxLayout()
        self.ollama_model = QComboBox()
        self.ollama_model.setEditable(True)
        self.ollama_model.addItems([
            "gpt-oss:20b", "llama3.2", "llama3.2:1b", "llama3.2:3b",
            "llama3.1", "llama3.1:70b", "llama3.1:405b",
            "mistral", "mixtral", "codellama",
            "phi3", "qwen2.5", "gemma2"
        ])
        self.ollama_model.setCurrentText(getattr(self.settings, 'ollama_model', 'llama3.2'))
        self.ollama_model.setFixedHeight(32)
        model_layout.addWidget(self.ollama_model)

        ollama_refresh_btn = QPushButton("↻")
        ollama_refresh_btn.setFixedSize(32, 32)
        ollama_refresh_btn.setToolTip("Refresh available models")
        ollama_refresh_btn.clicked.connect(lambda: self.refresh_ollama_models())
        model_layout.addWidget(ollama_refresh_btn)

        ollama_form.addRow("Model:", model_layout)

        container_layout.addWidget(ollama_group)
        
        # LM Studio
        lm_group = self.create_group("LM Studio (Local)")
        lm_form = QFormLayout(lm_group)
        lm_form.setSpacing(12)

        self.lmstudio_url = QLineEdit(self.settings.lmstudio_url)
        self.lmstudio_url.setFixedHeight(32)
        lm_form.addRow("URL:", self.lmstudio_url)

        lm_hint = QLabel("Default: http://localhost:1234/v1")
        lm_hint.setStyleSheet("font-size: 12px;")
        lm_form.addRow("", lm_hint)

        # Model selection with refresh button
        lm_model_layout = QHBoxLayout()
        self.lmstudio_model = QComboBox()
        self.lmstudio_model.setEditable(True)
        self.lmstudio_model.addItems([
            "local-model", "llama-3.2-1b", "llama-3.2-3b",
            "mistral-7b", "phi-3-mini"
        ])
        self.lmstudio_model.setCurrentText(getattr(self.settings, 'lmstudio_model', 'local-model'))
        self.lmstudio_model.setFixedHeight(32)
        lm_model_layout.addWidget(self.lmstudio_model)

        lm_refresh_btn = QPushButton("↻")
        lm_refresh_btn.setFixedSize(32, 32)
        lm_refresh_btn.setToolTip("Refresh available models")
        lm_refresh_btn.clicked.connect(lambda: self.refresh_lmstudio_models())
        lm_model_layout.addWidget(lm_refresh_btn)

        lm_form.addRow("Model:", lm_model_layout)

        lm_save_hint = QLabel("Click Save at the bottom for chat to use this URL and model.")
        lm_save_hint.setStyleSheet("font-size: 11px; color: #8E8E93; font-style: italic;")
        lm_save_hint.setWordWrap(True)
        lm_form.addRow("", lm_save_hint)

        container_layout.addWidget(lm_group)
        
        # OpenAI
        openai_group = self.create_group("OpenAI")
        openai_form = QFormLayout(openai_group)
        openai_form.setSpacing(12)

        self.openai_key = QLineEdit(self.settings.openai_api_key or "")
        self.openai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key.setFixedHeight(32)
        openai_form.addRow("API Key:", self.openai_key)

        openai_hint = QLabel("From platform.openai.com")
        openai_hint.setStyleSheet("font-size: 12px;")
        openai_form.addRow("", openai_hint)

        # Model selection with refresh button
        openai_model_layout = QHBoxLayout()
        self.openai_model = QComboBox()
        self.openai_model.setEditable(True)
        self.openai_model.addItems([
            "gpt-4o", "gpt-4o-mini", "gpt-4-turbo",
            "gpt-4", "gpt-3.5-turbo"
        ])
        self.openai_model.setCurrentText(getattr(self.settings, 'openai_model', 'gpt-4o'))
        self.openai_model.setFixedHeight(32)
        openai_model_layout.addWidget(self.openai_model)

        openai_refresh_btn = QPushButton("↻")
        openai_refresh_btn.setFixedSize(32, 32)
        openai_refresh_btn.setToolTip("Refresh available models")
        openai_refresh_btn.clicked.connect(lambda: self.refresh_openai_models())
        openai_model_layout.addWidget(openai_refresh_btn)

        openai_form.addRow("Model:", openai_model_layout)

        container_layout.addWidget(openai_group)
        
        # Anthropic
        anthropic_group = self.create_group("Anthropic")
        anthropic_form = QFormLayout(anthropic_group)
        anthropic_form.setSpacing(12)

        self.anthropic_key = QLineEdit(self.settings.anthropic_api_key or "")
        self.anthropic_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.anthropic_key.setFixedHeight(32)
        anthropic_form.addRow("API Key:", self.anthropic_key)

        anthropic_hint = QLabel("From console.anthropic.com")
        anthropic_hint.setStyleSheet("font-size: 12px;")
        anthropic_form.addRow("", anthropic_hint)

        # Model selection (Anthropic has fixed models, no refresh needed)
        self.anthropic_model = QComboBox()
        self.anthropic_model.setEditable(True)
        self.anthropic_model.addItems([
            "claude-sonnet-4-5-20250929", "claude-opus-4-6",
            "claude-sonnet-4-20250514", "claude-haiku-4-5-20251001",
            "claude-opus-4-5-20251101", "claude-opus-4-20250514",
        ])
        self.anthropic_model.setCurrentText(
            getattr(self.settings, 'anthropic_model', 'claude-sonnet-4-5-20250929')
        )
        self.anthropic_model.setFixedHeight(32)
        anthropic_form.addRow("Model:", self.anthropic_model)

        container_layout.addWidget(anthropic_group)
        
        # OpenRouter
        or_group = self.create_group("OpenRouter")
        or_form = QFormLayout(or_group)
        or_form.setSpacing(12)

        self.openrouter_key = QLineEdit(self.settings.openrouter_api_key or "")
        self.openrouter_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.openrouter_key.setFixedHeight(32)
        or_form.addRow("API Key:", self.openrouter_key)

        or_hint = QLabel("From openrouter.ai")
        or_hint.setStyleSheet("font-size: 12px;")
        or_form.addRow("", or_hint)

        self.openrouter_model = QComboBox()
        self.openrouter_model.setEditable(True)
        self.openrouter_model.addItems([
            "openai/gpt-4o", "openai/gpt-4o-mini",
            "anthropic/claude-3.5-sonnet", "anthropic/claude-3-opus",
            "google/gemini-pro-1.5", "meta-llama/llama-3.1-70b-instruct"
        ])
        self.openrouter_model.setCurrentText(getattr(self.settings, 'openrouter_model', 'openai/gpt-4o'))
        self.openrouter_model.setFixedHeight(32)
        or_form.addRow("Model:", self.openrouter_model)

        container_layout.addWidget(or_group)

        # Custom Provider
        custom_group = self.create_group("Custom Provider")
        custom_form = QFormLayout(custom_group)
        custom_form.setSpacing(12)

        self.custom_url = QLineEdit(getattr(self.settings, 'custom_provider_url', '') or "")
        self.custom_url.setPlaceholderText("https://api.example.com/v1")
        self.custom_url.setFixedHeight(32)
        custom_form.addRow("URL:", self.custom_url)

        custom_hint = QLabel("Base URL for the API endpoint")
        custom_hint.setStyleSheet("font-size: 12px;")
        custom_form.addRow("", custom_hint)

        self.custom_key = QLineEdit(getattr(self.settings, 'custom_provider_api_key', '') or "")
        self.custom_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.custom_key.setPlaceholderText("Optional API key")
        self.custom_key.setFixedHeight(32)
        custom_form.addRow("API Key:", self.custom_key)

        self.custom_model = QComboBox()
        self.custom_model.setEditable(True)
        self.custom_model.setCurrentText(getattr(self.settings, 'custom_provider_model', ''))
        self.custom_model.setPlaceholderText("model-name")
        self.custom_model.setFixedHeight(32)
        custom_form.addRow("Model:", self.custom_model)

        container_layout.addWidget(custom_group)
        container_layout.addStretch()
        
        scroll.setWidget(container)
        layout.addWidget(scroll)
    
    def create_group(self, title):
        group = QGroupBox(title)
        group.setStyleSheet(self.get_group_style())
        return group
    
    def get_group_style(self):
        dialog = self.window()
        if isinstance(dialog, SettingsDialog) and dialog.is_dark:
            return """
                QGroupBox {
                    font-weight: 600;
                    font-size: 13px;
                    border: 1px solid #3A3A3C;
                    border-radius: 6px;
                    margin-top: 8px;
                    margin-bottom: 8px;
                    padding: 8px 16px 16px 16px;
                    background: #2C2C2E;
                }
                QGroupBox::title {
                    subcontrol-origin: padding;
                    left: 0px;
                    top: 0px;
                    padding-bottom: 4px;
                    color: #FFFFFF;
                }
            """
        else:
            return """
                QGroupBox {
                    font-weight: 600;
                    font-size: 13px;
                    border: 1px solid #E5E5EA;
                    border-radius: 6px;
                    margin-top: 8px;
                    margin-bottom: 8px;
                    padding: 8px 16px 16px 16px;
                    background: #FAFAFA;
                }
                QGroupBox::title {
                    subcontrol-origin: padding;
                    left: 0px;
                    top: 0px;
                    padding-bottom: 4px;
                    color: #1C1C1E;
                }
            """
    
    def refresh_ollama_models(self):
        """Fetch available models from Ollama"""
        url = self.ollama_url.text()
        provider = OllamaProvider(url)

        # Store current selection
        current_model = self.ollama_model.currentText()

        # Disable button while fetching
        sender = self.sender()
        if sender:
            sender.setEnabled(False)
            sender.setText("...")

        # Create worker thread
        self.ollama_worker = ModelFetchWorker(provider)
        self.ollama_worker.finished.connect(
            lambda models: self.on_models_fetched(models, self.ollama_model, current_model, sender)
        )
        self.ollama_worker.error.connect(
            lambda error: self.on_models_fetch_error(error, "Ollama", sender)
        )
        self.ollama_worker.start()

    def refresh_lmstudio_models(self):
        """Fetch available models from LM Studio"""
        url = self.lmstudio_url.text()
        provider = LMStudioProvider(url)

        # Store current selection
        current_model = self.lmstudio_model.currentText()

        # Disable button while fetching
        sender = self.sender()
        if sender:
            sender.setEnabled(False)
            sender.setText("...")

        # Create worker thread
        self.lmstudio_worker = ModelFetchWorker(provider)
        self.lmstudio_worker.finished.connect(
            lambda models: self.on_models_fetched(models, self.lmstudio_model, current_model, sender)
        )
        self.lmstudio_worker.error.connect(
            lambda error: self.on_models_fetch_error(error, "LM Studio", sender)
        )
        self.lmstudio_worker.start()

    def refresh_openai_models(self):
        """Fetch available models from OpenAI"""
        api_key = self.openai_key.text()
        if not api_key:
            QMessageBox.warning(self, "API Key Required", "Please enter your OpenAI API key first")
            return

        provider = OpenAIProvider(api_key)

        # Store current selection
        current_model = self.openai_model.currentText()

        # Disable button while fetching
        sender = self.sender()
        if sender:
            sender.setEnabled(False)
            sender.setText("...")

        # Create worker thread
        self.openai_worker = ModelFetchWorker(provider)
        self.openai_worker.finished.connect(
            lambda models: self.on_models_fetched(models, self.openai_model, current_model, sender)
        )
        self.openai_worker.error.connect(
            lambda error: self.on_models_fetch_error(error, "OpenAI", sender)
        )
        self.openai_worker.start()

    def on_models_fetched(self, models, combo, current_model, button):
        """Handle successful model fetch"""
        # Re-enable button
        if button:
            button.setEnabled(True)
            button.setText("↻")

        # Clear and populate combo box
        combo.clear()
        if models:
            model_names = [m.get('name', m.get('id', 'Unknown')) for m in models]
            combo.addItems(model_names)

            # Restore previous selection if it exists
            if current_model and current_model in model_names:
                combo.setCurrentText(current_model)
        else:
            QMessageBox.information(self, "No Models", "No models found for this provider")

    def on_models_fetch_error(self, error, provider_name, button):
        """Handle model fetch error"""
        # Re-enable button
        if button:
            button.setEnabled(True)
            button.setText("↻")

        QMessageBox.warning(
            self,
            "Connection Error",
            f"Could not fetch models from {provider_name}:\n{error}\n\nMake sure the service is running."
        )

    def get_settings(self):
        return {
            "ollama_url": self.ollama_url.text(),
            "ollama_model": self.ollama_model.currentText(),
            "lmstudio_url": _normalize_lmstudio_url(self.lmstudio_url.text()),
            "lmstudio_model": self.lmstudio_model.currentText(),
            "openai_api_key": self.openai_key.text() or None,
            "openai_model": self.openai_model.currentText(),
            "anthropic_api_key": self.anthropic_key.text() or None,
            "anthropic_model": self.anthropic_model.currentText(),
            "openrouter_api_key": self.openrouter_key.text() or None,
            "openrouter_model": self.openrouter_model.currentText(),
            "custom_provider_url": self.custom_url.text() or None,
            "custom_provider_api_key": self.custom_key.text() or None,
            "custom_provider_model": self.custom_model.currentText(),
        }


class TelegramTab(SettingsTab):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)
        
        form = QFormLayout()
        form.setSpacing(16)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        # Bot Token
        self.bot_token = QLineEdit(self.settings.telegram_bot_token or "")
        self.bot_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.bot_token.setFixedHeight(32)
        form.addRow("Bot Token:", self.bot_token)
        
        token_hint = QLabel("Get from @BotFather on Telegram")
        token_hint.setStyleSheet("font-size: 12px;")
        form.addRow("", token_hint)
        
        # Webhook URL
        self.webhook_url = QLineEdit(self.settings.telegram_webhook_url or "")
        self.webhook_url.setFixedHeight(32)
        form.addRow("Webhook URL:", self.webhook_url)
        
        webhook_hint = QLabel("Leave empty for polling mode")
        webhook_hint.setStyleSheet("font-size: 12px;")
        form.addRow("", webhook_hint)
        
        llm_hint = QLabel("Note: Your LLM (LM Studio, Ollama, etc.) must be running for the bot to reply to messages.")
        llm_hint.setStyleSheet("font-size: 11px; color: #666;")
        llm_hint.setWordWrap(True)
        form.addRow("", llm_hint)
        
        config_path = get_config_path()
        config_hint = QLabel(f"Config: {config_path}")
        config_hint.setStyleSheet("font-size: 10px; color: #999;")
        config_hint.setWordWrap(True)
        form.addRow("", config_hint)
        
        layout.addLayout(form)
        
        # Test button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.test_btn = QPushButton("Test Connection")
        self.test_btn.setFixedSize(150, 32)
        self.test_btn.clicked.connect(self.test_connection)
        btn_layout.addWidget(self.test_btn)
        
        layout.addLayout(btn_layout)
        layout.addStretch()
    
    def test_connection(self):
        token = _sanitize_telegram_token(self.bot_token.text())
        if not token:
            QMessageBox.warning(self, "Error", "Please enter a bot token")
            return
        self.test_btn.setEnabled(False)
        self.test_btn.setText("Testing...")
        self._telegram_worker = TelegramTestWorker(token)
        self._telegram_worker.finished.connect(self._on_telegram_test_finished)
        self._telegram_worker.start()

    def _on_telegram_test_finished(self, success: bool, message: str):
        self.test_btn.setEnabled(True)
        self.test_btn.setText("Test Connection")
        if success:
            QMessageBox.information(self, "Telegram Test", message)
        else:
            QMessageBox.warning(
                self,
                "Telegram Test Failed",
                f"Could not connect to Telegram:\n\n{message}\n\n"
                "Check that your bot token is correct (from @BotFather).",
            )
    
    def get_settings(self):
        return {
            "telegram_bot_token": _sanitize_telegram_token(self.bot_token.text()),
            "telegram_webhook_url": self.webhook_url.text().strip() or None,
        }

class WhatsAppTab(SettingsTab):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)
        
        form = QFormLayout()
        form.setSpacing(16)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        # Session Path
        self.session_path = QLineEdit(self.settings.whatsapp_session_path or "")
        self.session_path.setFixedHeight(32)
        form.addRow("Session Path:", self.session_path)
        
        session_hint = QLabel("Directory to store WhatsApp session data (~ expands to home)")
        session_hint.setStyleSheet("font-size: 12px;")
        form.addRow("", session_hint)
        
        layout.addLayout(form)
        
        # Test button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        test_btn = QPushButton("Test Connection")
        test_btn.setFixedSize(150, 32)
        test_btn.clicked.connect(self.test_connection)
        btn_layout.addWidget(test_btn)
        
        layout.addLayout(btn_layout)
        layout.addStretch()
    
    def test_connection(self):
        path = self.session_path.text()
        if not path:
            QMessageBox.warning(self, "Error", "Please enter a session path")
            return
        QMessageBox.information(self, "Test", "WhatsApp session path configured")
    
    def get_settings(self):
        return {
            "whatsapp_session_path": self.session_path.text() or "~/.grizzyclaw/whatsapp_session",
        }

from PyQt6.QtWidgets import QHeaderView, QTextEdit, QPlainTextEdit, QGroupBox, QCheckBox, QListWidget, QListWidgetItem, QPushButton, QHBoxLayout, QInputDialog, QTreeWidget, QTreeWidgetItem
import json
import os
import signal
import subprocess
from pathlib import Path
from PyQt6.QtCore import Qt, QTimer
from .mcp_servers_dialog import MCPTab
from PyQt6.QtGui import QShowEvent

class PromptsTab(SettingsTab):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)
        
        form = QFormLayout()
        form.setSpacing(16)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        # System Prompt
        self.system_prompt_edit = QTextEdit()
        self.system_prompt_edit.setPlainText(self.settings.system_prompt)
        self.system_prompt_edit.setMaximumHeight(250)
        self.system_prompt_edit.setPlaceholderText("Enter custom system prompt for the agent...")
        form.addRow("System Prompt:", self.system_prompt_edit)
        
        # Rules File
        self.rules_file = QLineEdit(self.settings.rules_file or "")
        self.rules_file.setFixedHeight(32)
        form.addRow("Rules File:", self.rules_file)
        
        layout.addLayout(form)
        
        # Test button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        test_btn = QPushButton("Preview")
        test_btn.setFixedSize(150, 32)
        test_btn.clicked.connect(self.test_prompt)
        btn_layout.addWidget(test_btn)
        layout.addLayout(btn_layout)
        layout.addStretch()
    
    def test_prompt(self):
        QMessageBox.information(self, "Preview", "System prompt loaded successfully.")
    
    def get_settings(self):
        return {
            "system_prompt": self.system_prompt_edit.toPlainText(),
            "rules_file": self.rules_file.text() or None,
        }


class SkillConfigDialog(QDialog):
    """Schema-driven dialog to configure a single skill (from config_schema)."""
    def __init__(self, skill_id: str, skill_name: str, config_schema: dict, current_config: dict, parent=None):
        super().__init__(parent)
        self.skill_id = skill_id
        self.config_schema = config_schema
        self.current_config = dict(current_config) if current_config else {}
        self.setWindowTitle(f"Configure: {skill_name}")
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.widgets: dict = {}
        props = (config_schema or {}).get("properties") or {}
        for key, spec in props.items():
            if not isinstance(spec, dict):
                continue
            title = spec.get("title") or key.replace("_", " ").title()
            typ = (spec.get("type") or "string").lower()
            desc = spec.get("description", "")
            if typ == "boolean":
                w = QCheckBox()
                w.setChecked(bool(self.current_config.get(key, False)))
                self.widgets[key] = ("bool", w)
            elif typ == "integer" or typ == "number":
                w = QSpinBox()
                w.setRange(-999999, 999999)
                w.setValue(int(self.current_config.get(key, 0)))
                self.widgets[key] = ("number", w)
            else:
                w = QLineEdit()
                w.setPlaceholderText(desc or title)
                w.setText(str(self.current_config.get(key, "")))
                self.widgets[key] = ("str", w)
            form.addRow(QLabel(title), w)
        layout.addLayout(form)
        btns = QHBoxLayout()
        btns.addStretch()
        ok_btn = QPushButton("Save")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(cancel_btn)
        btns.addWidget(ok_btn)
        layout.addLayout(btns)

    def get_config(self) -> dict:
        out = {}
        for key, (kind, w) in self.widgets.items():
            if kind == "bool":
                out[key] = w.isChecked()
            elif kind == "number":
                out[key] = w.value()
            else:
                out[key] = w.text().strip() or None
        return {k: v for k, v in out.items() if v is not None}


class ClawHubTab(SettingsTab):
    """ClawHub skills registry and HuggingFace token only."""
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.is_dark = False
        theme = getattr(self.settings, "theme", "Light")
        self.is_dark = theme in ["Dark", "High Contrast Dark", "Dracula", "Monokai", "Nord", "Solarized Dark"]
        self._setup_theme_colors()
        self.setup_ui()

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
        else:
            self.bg_color = "#FFFFFF"
            self.fg_color = "#1C1C1E"
            self.card_bg = "#FAFAFA"
            self.border_color = "#E5E5EA"
            self.input_bg = "#FFFFFF"
            self.accent_color = "#007AFF"
            self.secondary_text = "#8E8E93"
            self.hover_bg = "#F5F5F7"

    def _create_card(self, title, description):
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{ background: {self.card_bg}; border: 1px solid {self.border_color};
                border-radius: 12px; padding: 16px; }}
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
            QLineEdit {{ padding: 0 12px; border: 1px solid {self.border_color};
                border-radius: 8px; background: {self.input_bg}; color: {self.fg_color}; font-size: 13px; }}
            QLineEdit:focus {{ border: 2px solid {self.accent_color}; }}
        """

    def _list_style(self):
        return f"""
            QListWidget {{ border: 1px solid {self.border_color}; border-radius: 8px;
                background: {self.input_bg}; color: {self.fg_color}; padding: 4px; }}
            QListWidget::item {{ padding: 8px 12px; border-radius: 4px; color: {self.fg_color}; }}
            QListWidget::item:selected {{ background: {self.accent_color}; color: white; }}
            QListWidget::item:hover:!selected {{ background: {self.hover_bg}; }}
        """

    def _secondary_btn_style(self):
        btn_bg = "#3A3A3C" if self.is_dark else "#F5F5F7"
        btn_hover = "#48484A" if self.is_dark else "#E5E5EA"
        return f"""
            QPushButton {{ background: {btn_bg}; color: {self.fg_color};
                border: 1px solid {self.border_color}; border-radius: 8px; padding: 8px 16px; font-size: 13px; }}
            QPushButton:hover {{ background: {btn_hover}; }}
        """

    def _icon_btn_style(self):
        btn_bg = "#3A3A3C" if self.is_dark else "#F5F5F7"
        btn_hover = "#48484A" if self.is_dark else "#E5E5EA"
        return f"""
            QPushButton {{ background: {btn_bg}; border: 1px solid {self.border_color};
                border-radius: 8px; font-size: 14px; color: {self.fg_color}; }}
            QPushButton:hover {{ background: {btn_hover}; }}
        """

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(20)
        self.setStyleSheet(f"background-color: {self.bg_color};")
        header = QLabel("ClawHub - Skills")
        header.setFont(QFont("-apple-system", 18, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {self.fg_color}; background: transparent;")
        layout.addWidget(header)
        subtitle = QLabel("Configure HuggingFace and the built-in skills registry.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {self.secondary_text}; font-size: 13px; background: transparent;")
        layout.addWidget(subtitle)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 8, 0)
        scroll_layout.setSpacing(20)
        hf_card = self._create_card("🤗 Hugging Face", "Access HuggingFace models and spaces")
        hf_layout = hf_card.layout()
        token_row = QHBoxLayout()
        token_row.setSpacing(12)
        self.hf_token = QLineEdit(self.settings.hf_token or "")
        self.hf_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.hf_token.setPlaceholderText("Enter your HuggingFace API token")
        self.hf_token.setFixedHeight(36)
        self.hf_token.setStyleSheet(self._input_style())
        token_row.addWidget(self.hf_token)
        show_token_btn = QPushButton("👁")
        show_token_btn.setFixedSize(36, 36)
        show_token_btn.setCheckable(True)
        show_token_btn.setStyleSheet(self._icon_btn_style())
        show_token_btn.toggled.connect(lambda checked: self.hf_token.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        token_row.addWidget(show_token_btn)
        hf_layout.addLayout(token_row)
        hf_hint = QLabel("Get your token from huggingface.co/settings/tokens")
        hf_hint.setStyleSheet("color: #8E8E93; font-size: 12px;")
        hf_layout.addWidget(hf_hint)
        scroll_layout.addWidget(hf_card)
        skills_card = self._create_card("⚡ Skills Registry", "Discover and install AI capabilities from the built-in registry")
        skills_layout = skills_card.layout()
        skills_hint = QLabel("Skills: web_search, filesystem, documentation, browser, memory, scheduler, calendar, gmail, github, mcp_marketplace")
        skills_hint.setStyleSheet(f"color: {self.secondary_text}; font-size: 12px;")
        skills_layout.addWidget(skills_hint)
        self.skills_list = QListWidget()
        self.skills_list.setFixedHeight(100)
        self.skills_list.setStyleSheet(self._list_style())
        for skill in self.settings.enabled_skills:
            self.skills_list.addItem(skill)
        skills_layout.addWidget(self.skills_list)
        skills_btns = QHBoxLayout()
        skills_btns.setSpacing(8)
        add_skill_btn = QPushButton("+ Add Skill")
        add_skill_btn.clicked.connect(self.add_skill)
        add_skill_btn.setStyleSheet(self._secondary_btn_style())
        skills_btns.addWidget(add_skill_btn)
        remove_skill_btn = QPushButton("Remove")
        remove_skill_btn.clicked.connect(self.remove_skill)
        remove_skill_btn.setStyleSheet(self._secondary_btn_style())
        skills_btns.addWidget(remove_skill_btn)
        configure_skill_btn = QPushButton("Configure")
        configure_skill_btn.setToolTip("Configure selected skill (if it has options)")
        configure_skill_btn.clicked.connect(self.configure_skill)
        configure_skill_btn.setStyleSheet(self._secondary_btn_style())
        skills_btns.addWidget(configure_skill_btn)
        refresh_skills_btn = QPushButton("Refresh")
        refresh_skills_btn.setToolTip("Reload enabled skills from settings")
        refresh_skills_btn.clicked.connect(self.refresh_skills_list)
        refresh_skills_btn.setStyleSheet(self._secondary_btn_style())
        skills_btns.addWidget(refresh_skills_btn)
        check_updates_btn = QPushButton("Check for updates")
        check_updates_btn.setToolTip("Check for newer skill versions (skills with update_url)")
        check_updates_btn.clicked.connect(self.check_skill_updates)
        check_updates_btn.setStyleSheet(self._secondary_btn_style())
        skills_btns.addWidget(check_updates_btn)
        skills_btns.addStretch()
        skills_layout.addLayout(skills_btns)
        scroll_layout.addWidget(skills_card)
        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)

    def refresh_skills_list(self):
        """Reload enabled skills list from current settings."""
        self.skills_list.clear()
        for skill in self.settings.enabled_skills:
            self.skills_list.addItem(skill)
        QMessageBox.information(self, "Refresh", "Skills list reloaded from settings.")

    def add_skill(self):
        _prompt = (
            "Enter a skill id to enable (e.g. web_search, calendar, gmail, filesystem, memory, "
            "scheduler, browser, documentation, github, mcp_marketplace):"
        )
        try:
            from grizzyclaw.skills.registry import get_available_skills
            skills = get_available_skills()
            enabled = [self.skills_list.item(i).text().strip().lower() for i in range(self.skills_list.count())]
            available = [s for s in skills if s.id.lower() not in enabled]
            if not available:
                QMessageBox.information(self, "Add Skill", "All available skills are already enabled.")
                return
            items = [f"{s.icon} {s.name} — {s.description}" for s in available]
            if not items:
                skill, ok = QInputDialog.getText(self, "Add Skill", _prompt)
                if ok and skill.strip():
                    sid = skill.strip()
                    if sid.lower() not in enabled:
                        self.skills_list.addItem(sid)
                        QMessageBox.information(self, "Add Skill", f"Added \"{sid}\". The skill is now enabled. Some skills need API keys or other setup in Settings → Integrations.")
                return
            choice, ok = QInputDialog.getItem(
                self, "Add Skill", "Select a skill to add (only skills not already enabled are shown):",
                items, 0, False
            )
            if ok and choice:
                for s in available:
                    if f"{s.icon} {s.name} — {s.description}" == choice:
                        if s.id.lower() not in enabled:
                            self.skills_list.addItem(s.id)
                            QMessageBox.information(self, "Add Skill", f"Added \"{s.name}\". Some skills (e.g. calendar, gmail) need credentials in Settings → Integrations.")
                        break
        except Exception:
            skill, ok = QInputDialog.getText(self, "Add Skill", _prompt)
            if ok and skill.strip():
                enabled = [self.skills_list.item(i).text().strip().lower() for i in range(self.skills_list.count())]
                if skill.strip().lower() not in enabled:
                    self.skills_list.addItem(skill.strip())
                    QMessageBox.information(self, "Add Skill", f"Added \"{skill.strip()}\". The skill is now enabled. Some skills need API keys or other setup in Settings → Integrations.")

    def remove_skill(self):
        row = self.skills_list.currentRow()
        if row >= 0:
            self.skills_list.takeItem(row)

    def configure_skill(self):
        """Open config_schema-driven dialog for selected skill and persist to skills.json."""
        row = self.skills_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "Configure Skill", "Select a skill from the list first.")
            return
        skill_id = self.skills_list.item(row).text().strip()
        try:
            from grizzyclaw.skills.registry import get_skill, load_user_skills, save_user_skills
            meta = get_skill(skill_id)
            if not meta or not getattr(meta, "config_schema", None):
                QMessageBox.information(
                    self, "Configure Skill",
                    f'Skill "{skill_id}" has no configurable options.',
                )
                return
            data_dir = Path(getattr(self.settings, "data_dir", None) or (Path.home() / ".grizzyclaw"))
            user_skills = load_user_skills(data_dir)
            current = user_skills.get(skill_id, {})
            dlg = SkillConfigDialog(
                skill_id, meta.name, meta.config_schema, current, self,
            )
            if dlg.exec() == QDialog.DialogCode.Accepted:
                config = dlg.get_config()
                user_skills[skill_id] = config
                if save_user_skills(user_skills, data_dir):
                    QMessageBox.information(self, "Configure Skill", f'Saved options for "{meta.name}".')
                else:
                    QMessageBox.warning(self, "Configure Skill", "Failed to save skills.json.")
        except Exception as e:
            QMessageBox.warning(self, "Configure Skill", f"Error: {e}")

    def check_skill_updates(self):
        """Check enabled skills for newer versions and show update prompt if any."""
        try:
            from grizzyclaw.skills.registry import get_skill, get_skill_version, check_skill_update
            lines = []
            for i in range(self.skills_list.count()):
                skill_id = self.skills_list.item(i).text().strip()
                meta = get_skill(skill_id)
                ver = get_skill_version(skill_id) if meta else None
                if ver:
                    lines.append(f"{meta.name if meta else skill_id}: v{ver}")
                result = check_skill_update(skill_id)
                if result:
                    cur, latest = result
                    lines.append(f"  → Update available: {cur} → {latest}")
            if not lines:
                QMessageBox.information(
                    self, "Skill versions",
                    "No version info or updatable skills. Add version/update_url to skills to see updates.",
                )
            else:
                QMessageBox.information(
                    self, "Skill versions",
                    "Versions and updates:\n\n" + "\n".join(lines),
                )
        except Exception as e:
            QMessageBox.warning(self, "Check for updates", f"Error: {e}")

    def get_settings(self):
        return {
            "hf_token": self.hf_token.text() or None,
            "enabled_skills": [self.skills_list.item(i).text() for i in range(self.skills_list.count())],
        }


class SecurityTab(SettingsTab):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(40, 24, 40, 24)
        container_layout.setSpacing(24)

        warning = QLabel("⚠️  Changes require restart")
        warning.setStyleSheet("font-weight: 600; padding: 12px; border-radius: 6px;")
        warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
        container_layout.addWidget(warning)

        security_group = QGroupBox("Security")
        security_group.setStyleSheet(self.get_group_style())
        form = QFormLayout(security_group)
        form.setSpacing(18)

        self.secret_key = QLineEdit(self.settings.secret_key)
        self.secret_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_key.setFixedHeight(32)
        form.addRow("Secret Key:", self.secret_key)
        self.jwt_secret = QLineEdit(self.settings.jwt_secret)
        self.jwt_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.jwt_secret.setFixedHeight(32)
        form.addRow("JWT Secret:", self.jwt_secret)
        self.rate_limit = QSpinBox()
        self.rate_limit.setRange(10, 1000)
        self.rate_limit.setValue(self.settings.rate_limit_requests)
        self.rate_limit.setFixedHeight(32)
        form.addRow("Rate Limit:", self.rate_limit)
        self.exec_commands_enabled = QCheckBox("Allow shell commands")
        self.exec_commands_enabled.setChecked(getattr(self.settings, "exec_commands_enabled", False))
        self.exec_commands_enabled.setToolTip("Let the agent run shell commands. Each command requires your approval in a dialog.")
        form.addRow("", self.exec_commands_enabled)
        self.exec_safe_commands_skip_approval = QCheckBox("Skip approval for safe commands (ls, df, pwd, whoami, date, etc.)")
        self.exec_safe_commands_skip_approval.setChecked(getattr(self.settings, "exec_safe_commands_skip_approval", True))
        form.addRow("", self.exec_safe_commands_skip_approval)
        self.exec_sandbox_enabled = QCheckBox("Run approved commands in sandbox (restricted PATH)")
        self.exec_sandbox_enabled.setChecked(getattr(self.settings, "exec_sandbox_enabled", False))
        form.addRow("", self.exec_sandbox_enabled)
        self.pre_send_health_check = QCheckBox("Check LLM provider before sending")
        self.pre_send_health_check.setChecked(getattr(self.settings, "pre_send_health_check", False))
        form.addRow("", self.pre_send_health_check)

        container_layout.addWidget(security_group)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        gen_btn = QPushButton("Generate New Keys")
        gen_btn.setFixedSize(140, 28)
        gen_btn.clicked.connect(self.generate_keys)
        btn_layout.addWidget(gen_btn)
        container_layout.addLayout(btn_layout)
        scroll.setWidget(container)
        layout.addWidget(scroll)

    def get_group_style(self):
        dialog = self.window()
        if isinstance(dialog, SettingsDialog) and getattr(dialog, "is_dark", False):
            return "QGroupBox { font-weight: 600; font-size: 13px; border: 1px solid #3A3A3C; border-radius: 6px; margin-top: 8px; margin-bottom: 8px; padding: 8px 16px 16px 16px; background: #2C2C2E; } QGroupBox::title { subcontrol-origin: padding; left: 0; top: 0; padding-bottom: 4px; color: #FFFFFF; }"
        return "QGroupBox { font-weight: 600; font-size: 13px; border: 1px solid #E5E5EA; border-radius: 6px; margin-top: 8px; margin-bottom: 8px; padding: 8px 16px 16px 16px; background: #FAFAFA; } QGroupBox::title { subcontrol-origin: padding; left: 0; top: 0; padding-bottom: 4px; color: #1C1C1E; }"

    def generate_keys(self):
        reply = QMessageBox.question(
            self, "Generate Keys",
            "This will invalidate sessions. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            import secrets
            self.secret_key.setText(secrets.token_urlsafe(32))
            self.jwt_secret.setText(secrets.token_urlsafe(32))
            QMessageBox.information(self, "Success", "New keys generated")
    
    def get_settings(self):
        return {
            "secret_key": self.secret_key.text(),
            "jwt_secret": self.jwt_secret.text(),
            "rate_limit_requests": self.rate_limit.value(),
            "exec_commands_enabled": self.exec_commands_enabled.isChecked(),
            "exec_safe_commands_skip_approval": self.exec_safe_commands_skip_approval.isChecked(),
            "exec_sandbox_enabled": self.exec_sandbox_enabled.isChecked(),
            "pre_send_health_check": self.pre_send_health_check.isChecked(),
        }


class IntegrationsTab(SettingsTab):
    """Media, transcription, Gmail Pub/Sub, gateway auth, and message queue settings."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(40, 24, 40, 24)
        container_layout.setSpacing(24)

        # Gateway & Queue
        gateway_group = QGroupBox("Gateway & Message Queue")
        gateway_group.setStyleSheet(self.get_group_style())
        gateway_form = QFormLayout(gateway_group)
        gateway_form.setSpacing(12)

        self.gateway_auth_token = QLineEdit(
            getattr(self.settings, "gateway_auth_token", None) or ""
        )
        self.gateway_auth_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.gateway_auth_token.setPlaceholderText("Optional Bearer token for sessions_send")
        self.gateway_auth_token.setFixedHeight(32)
        gateway_form.addRow("Gateway Auth Token:", self.gateway_auth_token)

        self.gateway_rate_limit = QSpinBox()
        self.gateway_rate_limit.setRange(10, 1000)
        self.gateway_rate_limit.setValue(
            getattr(self.settings, "gateway_rate_limit_requests", 60)
        )
        self.gateway_rate_limit.setFixedHeight(32)
        gateway_form.addRow("Rate limit (req/window):", self.gateway_rate_limit)

        self.gateway_rate_window = QSpinBox()
        self.gateway_rate_window.setRange(10, 3600)
        self.gateway_rate_window.setValue(
            getattr(self.settings, "gateway_rate_limit_window", 60)
        )
        self.gateway_rate_window.setFixedHeight(32)
        gateway_form.addRow("Rate window (seconds):", self.gateway_rate_window)

        self.queue_enabled = QCheckBox("Enable message queue (serialize per session)")
        self.queue_enabled.setChecked(getattr(self.settings, "queue_enabled", False))
        gateway_form.addRow("", self.queue_enabled)

        self.queue_max_per_session = QSpinBox()
        self.queue_max_per_session.setRange(1, 1000)
        self.queue_max_per_session.setValue(
            getattr(self.settings, "queue_max_per_session", 50)
        )
        self.queue_max_per_session.setFixedHeight(32)
        gateway_form.addRow("Queue max per session:", self.queue_max_per_session)

        container_layout.addWidget(gateway_group)

        # Media & Transcription
        media_group = QGroupBox("Media & Transcription")
        media_group.setStyleSheet(self.get_group_style())
        media_form = QFormLayout(media_group)
        media_form.setSpacing(18)

        self.transcription_provider = QComboBox()
        self.transcription_provider.addItems(["openai", "local"])
        self.transcription_provider.setCurrentText(
            getattr(self.settings, "transcription_provider", "openai")
        )
        self.transcription_provider.setFixedHeight(32)
        media_form.addRow("Transcription Provider:", self.transcription_provider)

        self.input_device_combo = QComboBox()
        self.input_device_combo.setFixedHeight(32)
        self._populate_input_devices()
        media_form.addRow("Microphone (voice input):", self.input_device_combo)
        input_device_hint = QLabel("If voice fails in app but works from terminal, select your mic explicitly.")
        input_device_hint.setStyleSheet("font-size: 11px; color: #8E8E93;")
        media_form.addRow("", input_device_hint)

        transcribe_hint = QLabel("local = Whisper on device (pip install openai-whisper)")
        transcribe_hint.setStyleSheet("font-size: 11px; color: #8E8E93;")
        media_form.addRow("", transcribe_hint)

        self.media_retention_days = QSpinBox()
        self.media_retention_days.setRange(1, 365)
        self.media_retention_days.setValue(
            getattr(self.settings, "media_retention_days", 7)
        )
        self.media_retention_days.setFixedHeight(32)
        media_form.addRow("Media Retention (days):", self.media_retention_days)

        self.media_max_size_mb = QSpinBox()
        self.media_max_size_mb.setRange(0, 10000)
        self.media_max_size_mb.setSpecialValueText("No limit")
        self.media_max_size_mb.setValue(
            getattr(self.settings, "media_max_size_mb", 0)
        )
        self.media_max_size_mb.setFixedHeight(32)
        media_form.addRow("Media max size (MB, 0=unlimited):", self.media_max_size_mb)

        media_hint = QLabel("openai = Whisper API; local = openai-whisper package")
        media_hint.setStyleSheet("font-size: 12px; color: #8E8E93;")
        media_form.addRow("", media_hint)

        container_layout.addWidget(media_group)

        # Voice (TTS) - ElevenLabs for high-quality synthesis
        voice_group = QGroupBox("Voice (TTS)")
        voice_group.setStyleSheet(self.get_group_style())
        voice_form = QFormLayout(voice_group)
        voice_form.setSpacing(18)
        self.elevenlabs_key = QLineEdit(
            getattr(self.settings, "elevenlabs_api_key", None) or ""
        )
        self.elevenlabs_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.elevenlabs_key.setPlaceholderText("Optional: ElevenLabs API key for high-quality TTS")
        self.elevenlabs_key.setFixedHeight(32)
        voice_form.addRow("ElevenLabs API Key:", self.elevenlabs_key)
        self.elevenlabs_voice = QLineEdit(
            getattr(self.settings, "elevenlabs_voice_id", "21m00Tcm4TlvDq8ikWAM") or ""
        )
        self.elevenlabs_voice.setPlaceholderText("Voice ID (default: Rachel)")
        self.elevenlabs_voice.setFixedHeight(32)
        voice_form.addRow("Voice ID:", self.elevenlabs_voice)
        self.tts_provider_combo = QComboBox()
        self.tts_provider_combo.addItems(["auto", "elevenlabs", "pyttsx3", "say"])
        self.tts_provider_combo.setCurrentText(
            getattr(self.settings, "tts_provider", "auto")
        )
        self.tts_provider_combo.setFixedHeight(32)
        voice_form.addRow("TTS Provider:", self.tts_provider_combo)
        voice_hint = QLabel("auto = try ElevenLabs → pyttsx3 → macOS say")
        voice_hint.setStyleSheet("font-size: 12px; color: #8E8E93;")
        voice_form.addRow("", voice_hint)
        container_layout.addWidget(voice_group)

        # Gmail Pub/Sub
        gmail_group = QGroupBox("Gmail Pub/Sub")
        gmail_group.setStyleSheet(self.get_group_style())
        gmail_form = QFormLayout(gmail_group)
        gmail_form.setSpacing(12)

        creds_row = QHBoxLayout()
        self.gmail_credentials_json = QLineEdit(
            getattr(self.settings, "gmail_credentials_json", None) or ""
        )
        self.gmail_credentials_json.setPlaceholderText("Path to OAuth token JSON")
        self.gmail_credentials_json.setFixedHeight(32)
        creds_row.addWidget(self.gmail_credentials_json)

        browse_btn = QPushButton("Browse...")
        browse_btn.setFixedHeight(32)
        browse_btn.clicked.connect(self._browse_gmail_credentials)
        creds_row.addWidget(browse_btn)
        gmail_form.addRow("Credentials JSON:", creds_row)

        self.gmail_pubsub_topic = QLineEdit(
            getattr(self.settings, "gmail_pubsub_topic", None) or ""
        )
        self.gmail_pubsub_topic.setPlaceholderText("e.g. projects/my-project/topics/gmail")
        self.gmail_pubsub_topic.setFixedHeight(32)
        gmail_form.addRow("Pub/Sub Topic:", self.gmail_pubsub_topic)

        self.gmail_pubsub_audience = QLineEdit(
            getattr(self.settings, "gmail_pubsub_audience", None) or ""
        )
        self.gmail_pubsub_audience.setPlaceholderText("https://your-host/gmail (for JWT verification)")
        self.gmail_pubsub_audience.setFixedHeight(32)
        gmail_form.addRow("Audience URL:", self.gmail_pubsub_audience)

        gmail_hint = QLabel("Leave empty to disable Gmail integration")
        gmail_hint.setStyleSheet("font-size: 12px; color: #8E8E93;")
        gmail_form.addRow("", gmail_hint)

        encrypt_btn = QPushButton("Encrypt credentials file")
        encrypt_btn.setFixedHeight(32)
        encrypt_btn.clicked.connect(self._encrypt_gmail_credentials)
        gmail_form.addRow("", encrypt_btn)

        container_layout.addWidget(gmail_group)

        # Automation Triggers
        triggers_group = QGroupBox("Automation Triggers")
        triggers_group.setStyleSheet(self.get_group_style())
        triggers_layout = QVBoxLayout(triggers_group)
        triggers_hint = QLabel(
            "Event-based automation: run actions when messages match conditions "
            "(e.g. message contains 'urgent' → call webhook)"
        )
        triggers_hint.setWordWrap(True)
        triggers_hint.setStyleSheet("font-size: 12px; color: #8E8E93;")
        triggers_layout.addWidget(triggers_hint)
        self.triggers_btn = QPushButton("Manage Triggers...")
        self.triggers_btn.setFixedHeight(36)
        self.triggers_btn.clicked.connect(self._open_triggers_dialog)
        triggers_layout.addWidget(self.triggers_btn)
        container_layout.addWidget(triggers_group)

        container_layout.addStretch()

        scroll.setWidget(container)
        layout.addWidget(scroll)

    def _populate_input_devices(self):
        """Populate microphone dropdown with available input devices."""
        try:
            from grizzyclaw.utils.audio_record import list_input_devices
            devices = list_input_devices()
            self.input_device_combo.clear()
            self.input_device_combo.addItem("System default", None)
            for idx, name in devices:
                self.input_device_combo.addItem(name, idx)
            saved_idx = getattr(self.settings, "input_device_index", None)
            saved_name = getattr(self.settings, "input_device_name", None)
            if saved_name:
                for i in range(self.input_device_combo.count()):
                    if self.input_device_combo.itemText(i) == saved_name:
                        self.input_device_combo.setCurrentIndex(i)
                        break
            elif saved_idx is not None:
                for i in range(self.input_device_combo.count()):
                    if self.input_device_combo.itemData(i) == saved_idx:
                        self.input_device_combo.setCurrentIndex(i)
                        break
        except Exception:
            self.input_device_combo.addItem("System default", None)

    def _open_triggers_dialog(self):
        """Open the automation triggers management dialog."""
        from grizzyclaw.gui.triggers_dialog import TriggersDialog

        dlg = TriggersDialog(parent=self)
        dlg.exec()

    def _browse_gmail_credentials(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Gmail OAuth Credentials",
            "",
            "JSON (*.json);;All files (*)",
        )
        if path:
            self.gmail_credentials_json.setText(path)

    def _encrypt_gmail_credentials(self):
        """Encrypt plain JSON credentials and save to .enc file."""
        path = self.gmail_credentials_json.text().strip()
        if not path:
            QMessageBox.warning(
                self, "No path",
                "Enter path to plain JSON credentials first, then click Encrypt.",
            )
            return
        from pathlib import Path
        p = Path(path).expanduser()
        if not p.exists():
            QMessageBox.warning(self, "File not found", f"File not found: {path}")
            return
        secret = getattr(self.settings, "secret_key", None)
        if not secret:
            QMessageBox.warning(
                self, "Secret key required",
                "Set a secret key in the Security tab first.",
            )
            return
        try:
            import json
            from grizzyclaw.automation.gmail_creds import save_gmail_credentials_encrypted
            data = json.loads(p.read_text(encoding="utf-8"))
            enc_path = str(Path.home() / ".grizzyclaw" / "gmail_credentials.enc")
            if save_gmail_credentials_encrypted(data, enc_path, secret):
                self.gmail_credentials_json.setText(enc_path)
                QMessageBox.information(
                    self, "Encrypted",
                    f"Credentials saved encrypted to:\n{enc_path}",
                )
            else:
                QMessageBox.warning(self, "Error", "Failed to encrypt credentials.")
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def get_group_style(self):
        dialog = self.window()
        if isinstance(dialog, SettingsDialog) and dialog.is_dark:
            return """
                QGroupBox {
                    font-weight: 600;
                    font-size: 13px;
                    border: 1px solid #3A3A3C;
                    border-radius: 6px;
                    margin-top: 8px;
                    margin-bottom: 8px;
                    padding: 8px 16px 16px 16px;
                    background: #2C2C2E;
                }
                QGroupBox::title {
                    subcontrol-origin: padding;
                    left: 0px;
                    top: 0px;
                    padding-bottom: 4px;
                    color: #FFFFFF;
                }
            """
        return """
            QGroupBox {
                font-weight: 600;
                font-size: 13px;
                border: 1px solid #E5E5EA;
                border-radius: 6px;
                margin-top: 8px;
                margin-bottom: 8px;
                padding: 8px 16px 16px 16px;
                background: #FAFAFA;
            }
            QGroupBox::title {
                subcontrol-origin: padding;
                left: 0px;
                top: 0px;
                padding-bottom: 4px;
                color: #1C1C1E;
            }
        """

    def get_settings(self):
        return {
            "gateway_auth_token": self.gateway_auth_token.text().strip() or None,
            "gateway_rate_limit_requests": self.gateway_rate_limit.value(),
            "gateway_rate_limit_window": self.gateway_rate_window.value(),
            "queue_enabled": self.queue_enabled.isChecked(),
            "queue_max_per_session": self.queue_max_per_session.value(),
            "transcription_provider": self.transcription_provider.currentText(),
            "input_device_index": self.input_device_combo.currentData(),
            "input_device_name": self.input_device_combo.currentText() if self.input_device_combo.currentData() is not None else None,
            "media_retention_days": self.media_retention_days.value(),
            "media_max_size_mb": self.media_max_size_mb.value(),
            "elevenlabs_api_key": self.elevenlabs_key.text().strip() or None,
            "elevenlabs_voice_id": self.elevenlabs_voice.text().strip() or "21m00Tcm4TlvDq8ikWAM",
            "tts_provider": self.tts_provider_combo.currentText(),
            "gmail_credentials_json": self.gmail_credentials_json.text().strip() or None,
            "gmail_pubsub_topic": self.gmail_pubsub_topic.text().strip() or None,
            "gmail_pubsub_audience": self.gmail_pubsub_audience.text().strip() or None,
        }


class DaemonTab(SettingsTab):
    """Daemon control: start, stop, status."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(20)

        daemon_group = QGroupBox("Background Daemon")
        daemon_group.setStyleSheet(self.get_group_style())
        form = QFormLayout(daemon_group)
        form.setSpacing(12)

        self.daemon_status_label = QLabel("Checking...")
        self.daemon_status_label.setStyleSheet("font-weight: 500;")
        form.addRow("Status:", self.daemon_status_label)

        btn_row = QHBoxLayout()
        self.daemon_start_btn = QPushButton("Start Daemon")
        self.daemon_start_btn.setFixedHeight(32)
        self.daemon_start_btn.clicked.connect(self._on_start_daemon)
        btn_row.addWidget(self.daemon_start_btn)

        self.daemon_stop_btn = QPushButton("Stop Daemon")
        self.daemon_stop_btn.setFixedHeight(32)
        self.daemon_stop_btn.clicked.connect(self._on_stop_daemon)
        btn_row.addWidget(self.daemon_stop_btn)
        btn_row.addStretch()
        form.addRow("", btn_row)

        hint = QLabel(
            "The daemon runs 24/7 in the background with Gateway, webhooks, and IPC. "
            "WebChat: http://127.0.0.1:18788/chat. "
            "If it stops, check ~/.grizzyclaw/daemon_stderr.log for errors."
        )
        hint.setStyleSheet("font-size: 12px; color: #8E8E93;")
        hint.setWordWrap(True)
        form.addRow("", hint)

        layout.addWidget(daemon_group)

        self._refresh_status()
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(3000)

    def get_group_style(self):
        dialog = self.window()
        if isinstance(dialog, SettingsDialog) and getattr(dialog, "is_dark", False):
            return """
                QGroupBox {
                    font-weight: 600;
                    border: 1px solid #3A3A3C;
                    border-radius: 8px;
                    margin-top: 12px;
                    padding-top: 12px;
                }
                QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
            """
        return """
            QGroupBox {
                font-weight: 600;
                border: 1px solid #E5E5EA;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 12px;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
        """

    def _refresh_status(self):
        try:
            from grizzyclaw.daemon.ipc import IPCClient
            client = IPCClient()
            running = client.is_daemon_running()
        except Exception:
            running = False
        if running:
            self.daemon_status_label.setText("Running")
            self.daemon_status_label.setStyleSheet("font-weight: 500; color: #34C759;")
            self.daemon_start_btn.setEnabled(False)
            self.daemon_stop_btn.setEnabled(True)
        else:
            self.daemon_status_label.setText("Stopped")
            self.daemon_status_label.setStyleSheet("font-weight: 500; color: #8E8E93;")
            self.daemon_start_btn.setEnabled(True)
            self.daemon_stop_btn.setEnabled(False)

    def _on_start_daemon(self):
        try:
            # When frozen (app bundle), sys.executable is the app; pass args directly.
            # When running from source, use python -m grizzyclaw daemon run.
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "daemon", "run"]
            else:
                cmd = [sys.executable, "-m", "grizzyclaw", "daemon", "run"]
            log_dir = Path.home() / ".grizzyclaw"
            log_dir.mkdir(parents=True, exist_ok=True)
            stderr_path = log_dir / "daemon_stderr.log"
            with open(stderr_path, "w") as f:
                subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    cwd=str(Path.home()),
                )
            self.daemon_status_label.setText("Starting...")
            QTimer.singleShot(2500, self._refresh_status)
        except Exception as e:
            QMessageBox.warning(self, "Start Daemon", f"Failed to start: {e}")

    def _on_stop_daemon(self):
        self.daemon_stop_btn.setEnabled(False)
        self._stop_worker = DaemonStopWorker()
        self._stop_worker.finished.connect(self._on_stop_finished)
        self._stop_worker.start()

    def _on_stop_finished(self, success: bool, message: str):
        self._refresh_status()
        if not success:
            QMessageBox.warning(self, "Stop Daemon", message)

    def get_settings(self):
        return {}


class DaemonStopWorker(QThread):
    """Worker to send IPC stop command."""
    finished = pyqtSignal(bool, str)

    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                from grizzyclaw.daemon.ipc import IPCClient
                client = IPCClient()
                result = loop.run_until_complete(client.send_command("stop"))
                if result.get("status") == "success":
                    self.finished.emit(True, "")
                else:
                    self.finished.emit(False, result.get("error", "Unknown error"))
            finally:
                loop.close()
        except Exception as e:
            self.finished.emit(False, str(e))


class AppearanceTab(SettingsTab):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(40, 24, 40, 24)
        container_layout.setSpacing(24)

        theme_group = self.create_group("Theme")
        theme_form = QFormLayout(theme_group)
        theme_form.setSpacing(18)
        self.theme_combo = QComboBox()
        self.theme_combo.setEditable(False)
        self.theme_combo.addItems([
            "Light", "Dark", "Auto (System)",
            "High Contrast Light", "High Contrast Dark",
            "Nord", "Solarized Light", "Solarized Dark", "Dracula", "Monokai"
        ])
        self.theme_combo.setCurrentText(getattr(self.settings, "theme", "Light"))
        self.theme_combo.setFixedHeight(32)
        self.theme_combo.currentTextChanged.connect(self.on_theme_changed)
        theme_form.addRow("Color Theme:", self.theme_combo)
        container_layout.addWidget(theme_group)

        font_group = self.create_group("Typography")
        font_form = QFormLayout(font_group)
        font_form.setSpacing(18)
        self.font_family = QComboBox()
        self.font_family.setEditable(False)
        self.font_family.addItems(["System Default", "SF Pro", "Helvetica", "Arial", "Inter"])
        self.font_family.setCurrentText(getattr(self.settings, "font_family", "System Default"))
        self.font_family.setFixedHeight(32)
        font_form.addRow("Font Family:", self.font_family)
        self.font_size = QSpinBox()
        self.font_size.setRange(10, 20)
        self.font_size.setValue(getattr(self.settings, "font_size", 13))
        self.font_size.setFixedHeight(32)
        font_form.addRow("Base Font Size:", self.font_size)
        container_layout.addWidget(font_group)

        density_group = self.create_group("UI Density")
        density_form = QFormLayout(density_group)
        density_form.setSpacing(18)
        self.compact_mode = QCheckBox("Enable Compact Mode")
        self.compact_mode.setChecked(getattr(self.settings, "compact_mode", False))
        density_form.addRow("", self.compact_mode)
        container_layout.addWidget(density_group)

        scroll.setWidget(container)
        layout.addWidget(scroll)
    
    def create_group(self, title):
        group = QGroupBox(title)
        group.setStyleSheet(self.get_group_style())
        return group
    
    def get_group_style(self):
        dialog = self.window()
        if isinstance(dialog, SettingsDialog) and dialog.is_dark:
            return """
                QGroupBox {
                    font-weight: 600;
                    font-size: 13px;
                    border: 1px solid #3A3A3C;
                    border-radius: 6px;
                    margin-top: 8px;
                    margin-bottom: 8px;
                    padding: 8px 16px 16px 16px;
                    background: #2C2C2E;
                }
                QGroupBox::title {
                    subcontrol-origin: padding;
                    left: 0px;
                    top: 0px;
                    padding-bottom: 4px;
                    color: #FFFFFF;
                }
            """
        else:
            return """
                QGroupBox {
                    font-weight: 600;
                    font-size: 13px;
                    border: 1px solid #E5E5EA;
                    border-radius: 6px;
                    margin-top: 8px;
                    margin-bottom: 8px;
                    padding: 8px 16px 16px 16px;
                    background: #FAFAFA;
                }
                QGroupBox::title {
                    subcontrol-origin: padding;
                    left: 0px;
                    top: 0px;
                    padding-bottom: 4px;
                    color: #1C1C1E;
                }
            """
    
    def on_theme_changed(self, theme):
        # Apply theme to dialog in real-time
        dialog = self.window()
        if isinstance(dialog, SettingsDialog):
            dialog.apply_theme_preview(theme)
    
    def get_settings(self):
        return {
            "theme": self.theme_combo.currentText(),
            "font_family": self.font_family.currentText(),
            "font_size": self.font_size.value(),
            "compact_mode": self.compact_mode.isChecked(),
        }


class SettingsDialog(QDialog):
    settings_saved = pyqtSignal()

    def __init__(self, settings, parent=None, theme_colors=None):
        super().__init__(parent)
        self.settings = settings
        self.is_dark = False
        self.setWindowTitle("Preferences")
        self.setMinimumSize(700, 550)
        self.resize(780, 600)
        self.setup_ui()
        # Apply theme to match main window
        if theme_colors:
            self.apply_theme_from_colors(theme_colors)
        else:
            self.apply_theme_preview(self.settings.theme)
    
    def setup_ui(self):
        self.setStyleSheet("""
            QDialog {
                background: #FFFFFF;
            }
            QLineEdit, QComboBox, QSpinBox {
                padding: 4px 8px;
                border: 1px solid #D1D1D6;
                border-radius: 4px;
                background: #FFFFFF;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border: 2px solid #007AFF;
                padding: 3px 7px;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 20px;
                border-left: 1px solid #D1D1D6;
                background: #FFFFFF;
            }
            QComboBox::down-arrow {
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #1C1C1E;
                width: 0px;
                height: 0px;
                margin-right: 6px;
            }
            QComboBox QAbstractItemView {
                background: #FFFFFF;
                color: #1C1C1E;
                border: 1px solid #D1D1D6;
                selection-background-color: #007AFF;
                selection-color: #FFFFFF;
                outline: none;
            }
            QGroupBox {
                font-weight: 600;
            }
            QLabel {
                color: #1C1C1E;
            }
            QPushButton {
                padding: 6px 16px;
                border: none;
                border-radius: 4px;
                background: #007AFF;
                color: white;
                font-weight: 500;
            }
            QPushButton:hover {
                background: #0051D5;
            }
            QPushButton:pressed {
                background: #003BB3;
            }
            QTabWidget::pane {
                border: none;
            }
            QTabBar::tab {
                padding: 12px 14px;
                min-width: 56px;
                border: none;
                background: transparent;
                color: #3C3C43;
                font-size: 15px;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                color: #007AFF;
                border-right: 3px solid #007AFF;
            }
            QTabBar::tab:hover:!selected {
                background: rgba(0,122,255,0.06);
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Header
        self.header = QWidget()
        self.header.setStyleSheet("background: #F5F5F7; border-bottom: 1px solid #E5E5EA;")
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(24, 16, 24, 16)
        
        self.title_label = QLabel("Preferences")
        self.title_label.setFont(QFont("-apple-system", 18, QFont.Weight.Bold))
        header_layout.addWidget(self.title_label)
        header_layout.addStretch()
        
        layout.addWidget(self.header)
        
        # Tabs: horizontal at top, 2 rows
        self.tabs = QTabWidget()
        self.tabs.tabBar().hide()
        self.tabs.setDocumentMode(True)
        
        self.general_tab = GeneralTab(self.settings)
        self.llm_tab = LLMTab(self.settings)
        self.telegram_tab = TelegramTab(self.settings)
        self.whatsapp_tab = WhatsAppTab(self.settings)
        self.prompts_tab = PromptsTab(self.settings)
        self.clawhub_tab = ClawHubTab(self.settings)
        self.mcp_tab = MCPTab(self.settings)
        self.security_tab = SecurityTab(self.settings)
        self.integrations_tab = IntegrationsTab(self.settings)
        self.daemon_tab = DaemonTab(self.settings)
        self.appearance_tab = AppearanceTab(self.settings)
        
        tab_items = [
            ("General", self.general_tab),
            ("LLM Providers", self.llm_tab),
            ("Telegram", self.telegram_tab),
            ("WhatsApp", self.whatsapp_tab),
            ("Appearance", self.appearance_tab),
            ("Daemon", self.daemon_tab),
            ("Prompts & Rules", self.prompts_tab),
            ("ClawHub", self.clawhub_tab),
            ("MCP Servers", self.mcp_tab),
            ("Security", self.security_tab),
            ("Integrations", self.integrations_tab),
        ]
        for label, widget in tab_items:
            self.tabs.addTab(widget, label)
        
        # Custom tab bar: 2 rows only (6 on first row, 5 on second)
        self.tab_bar = QWidget()
        self.tab_bar.setStyleSheet("background: #F5F5F7; border-bottom: 1px solid #E5E5EA;")
        tab_bar_layout = QGridLayout(self.tab_bar)
        tab_bar_layout.setContentsMargins(16, 8, 16, 8)
        tab_bar_layout.setSpacing(6)
        self._tab_btn_style_light = """
            QPushButton {
                padding: 8px 14px;
                border: none;
                background: transparent;
                color: #3C3C43;
                font-size: 13px;
                font-weight: 500;
                border-radius: 6px;
            }
            QPushButton:hover {
                background: rgba(0,122,255,0.08);
            }
            QPushButton:checked {
                background: rgba(0,122,255,0.12);
                color: #007AFF;
            }
        """
        self._tab_btn_style_dark = """
            QPushButton {
                padding: 8px 14px;
                border: none;
                background: transparent;
                color: #E5E5EA;
                font-size: 13px;
                font-weight: 500;
                border-radius: 6px;
            }
            QPushButton:hover {
                background: rgba(10,132,255,0.12);
            }
            QPushButton:checked {
                background: rgba(10,132,255,0.2);
                color: #0A84FF;
            }
        """
        tab_btn_style = self._tab_btn_style_dark if self.is_dark else self._tab_btn_style_light
        tab_btn_group = QButtonGroup(self.tab_bar)
        tab_btn_group.setExclusive(True)
        self._tab_buttons = []
        for i, (label, _) in enumerate(tab_items):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet(tab_btn_style)
            btn.clicked.connect(lambda checked, idx=i: self.tabs.setCurrentIndex(idx))
            tab_btn_group.addButton(btn)
            self._tab_buttons.append(btn)
            row, col = divmod(i, 6)
            tab_bar_layout.addWidget(btn, row, col)
        tab_bar_layout.setColumnStretch(5, 1)
        layout.addWidget(self.tab_bar)
        layout.addWidget(self.tabs, 1)
        self._tab_buttons[0].setChecked(True)
        self.tabs.currentChanged.connect(
            lambda idx: self._tab_buttons[idx].setChecked(True) if 0 <= idx < len(self._tab_buttons) else None
        )
        
        # Buttons
        self.btn_bar = QWidget()
        self.btn_bar.setStyleSheet("background: #F5F5F7; border-top: 1px solid #E5E5EA;")
        btn_layout = QHBoxLayout(self.btn_bar)
        btn_layout.setContentsMargins(24, 16, 24, 16)
        btn_layout.addStretch()
        
        self.close_btn = QPushButton("Close")
        self.close_btn.setFixedWidth(80)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #007AFF;
                border: 1px solid #C7C7CC;
            }
            QPushButton:hover {
                background: rgba(0,122,255,0.1);
            }
        """)
        self.close_btn.clicked.connect(self._save_and_close)
        
        self.save_btn = QPushButton("Save")
        self.save_btn.setFixedWidth(80)
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self.save_settings)
        
        btn_layout.addWidget(self.close_btn)
        btn_layout.addSpacing(8)
        btn_layout.addWidget(self.save_btn)
        
        layout.addWidget(self.btn_bar)
    
    def _collect_and_apply_settings(self):
        """Gather settings from all tabs and apply to self.settings (in memory)."""
        new_settings = {}
        new_settings.update(self.general_tab.get_settings())
        new_settings.update(self.llm_tab.get_settings())
        new_settings.update(self.telegram_tab.get_settings())
        new_settings.update(self.whatsapp_tab.get_settings())
        new_settings.update(self.prompts_tab.get_settings())
        new_settings.update(self.clawhub_tab.get_settings())
        new_settings.update(self.mcp_tab.get_settings())
        new_settings.update(self.security_tab.get_settings())
        new_settings.update(self.integrations_tab.get_settings())
        new_settings.update(self.daemon_tab.get_settings())
        new_settings.update(self.appearance_tab.get_settings())
        for key, value in new_settings.items():
            setattr(self.settings, key, value)
        return new_settings

    def _save_to_file(self) -> bool:
        """Persist current settings to disk. Returns True on success."""
        self._collect_and_apply_settings()
        try:
            config_path = get_config_path()
            self.settings.to_file(str(config_path))
            return True
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save: {e}")
            return False

    def save_settings(self):
        """Save to disk and stay open. Shows brief 'Saved' feedback."""
        if self._save_to_file():
            orig = self.save_btn.text()
            self.save_btn.setText("Saved!")
            QTimer.singleShot(1200, lambda: self.save_btn.setText(orig))
            self.apply_theme_preview(self.settings.theme)
            self.settings_saved.emit()

    def _save_and_close(self):
        """Save to disk and close. Main window will apply on accept()."""
        if self._save_to_file():
            self.accept()

    def closeEvent(self, event):
        """On X: save and close (same as Close button)."""
        if self._save_to_file():
            self.accept()
        event.accept()

    def reject(self):
        """On Escape: save and close (same as Close button)."""
        self._save_and_close()
    
    def get_settings(self):
        return self.settings

    def apply_theme_from_colors(self, theme_colors: dict):
        """Apply theme using the same color dict as the main window."""
        self.is_dark = theme_colors.get("is_dark", False)
        bg = theme_colors.get("bg", "#FFFFFF")
        fg = theme_colors.get("fg", "#1C1C1E")
        sidebar_bg = theme_colors.get("sidebar_bg", "#F5F5F7")
        border = theme_colors.get("border", "#E5E5EA")
        input_bg = theme_colors.get("input_bg", "#FFFFFF")
        input_border = theme_colors.get("input_border", "#D1D1D6")
        accent = theme_colors.get("accent", "#007AFF")
        secondary = theme_colors.get("secondary", "#8E8E93")

        self.setStyleSheet(f"""
            QDialog {{ background: {bg}; }}
            QWidget {{ background: {bg}; }}
            QLineEdit, QComboBox, QSpinBox {{
                padding: 4px 8px;
                border: 1px solid {input_border};
                border-radius: 4px;
                background: {input_bg};
                color: {fg};
            }}
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
                border: 2px solid {accent};
                padding: 3px 7px;
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 20px;
                border-left: 1px solid {input_border};
                background: {input_bg};
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid {fg};
                width: 0px;
                height: 0px;
                margin-right: 6px;
            }}
            QComboBox QAbstractItemView {{
                background: {input_bg};
                color: {fg};
                border: 1px solid {input_border};
                selection-background-color: {accent};
                selection-color: white;
                outline: none;
            }}
            QGroupBox {{
                font-weight: 600;
                background: {sidebar_bg};
                border: 1px solid {border};
                color: {fg};
            }}
            QLabel {{ color: {fg}; background: transparent; }}
            QFormLayout QLabel {{ color: {fg}; }}
            QPushButton {{
                padding: 6px 16px;
                border: none;
                border-radius: 4px;
                background: {accent};
                color: white;
                font-weight: 500;
            }}
            QPushButton:hover {{ opacity: 0.9; }}
            QPushButton:pressed {{ opacity: 0.8; }}
            QTabWidget::pane {{ border: none; background: {bg}; }}
            QTabBar::tab {{
                padding: 12px 14px;
                min-width: 56px;
                border: none;
                background: transparent;
                color: {secondary};
                font-size: 15px;
                font-weight: bold;
            }}
            QTabBar::tab:selected {{ color: {accent}; border-right: 3px solid {accent}; }}
            QTabBar::tab:hover:!selected {{ background: rgba(128,128,128,0.1); }}
            QCheckBox {{ color: {fg}; }}
            QCheckBox::indicator {{ border: 1px solid {input_border}; background: {input_bg}; border-radius: 3px; }}
            QCheckBox::indicator:checked {{ background: {accent}; border: 1px solid {accent}; }}
            QScrollArea {{ border: none; background: {bg}; }}
            QScrollArea > QWidget > QWidget {{ background: {bg}; }}
        """)
        self.update_group_styles()
        self.header.setStyleSheet(f"background: {sidebar_bg}; border-bottom: 1px solid {border};")
        self.title_label.setStyleSheet(f"color: {fg};")
        self.tab_bar.setStyleSheet(f"background: {sidebar_bg}; border-bottom: 1px solid {border};")
        self.btn_bar.setStyleSheet(f"background: {sidebar_bg}; border-top: 1px solid {border};")
        tab_btn_fg = fg
        tab_btn_accent = accent
        tab_btn_style = f"""
            QPushButton {{
                padding: 8px 14px;
                border: none;
                background: transparent;
                color: {tab_btn_fg};
                font-size: 13px;
                font-weight: 500;
                border-radius: 6px;
            }}
            QPushButton:hover {{ background: rgba(128,128,128,0.15); }}
            QPushButton:checked {{ background: rgba(128,128,128,0.2); color: {tab_btn_accent}; }}
        """
        if hasattr(self, "_tab_buttons"):
            for btn in self._tab_buttons:
                btn.setStyleSheet(tab_btn_style)
        self.close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                color: {accent};
                border: 1px solid {input_border};
            }}
            QPushButton:hover {{ background: rgba(128,128,128,0.1); }}
        """)

    def apply_theme_preview(self, theme):
        """Apply theme to the preferences dialog. Uses parent's get_theme_colors when available."""
        parent = self.parent()
        if parent and hasattr(parent, "get_theme_colors"):
            resolved = getattr(parent, "_resolve_theme", lambda t: t)(theme)
            theme_colors = parent.get_theme_colors(resolved)
            self.apply_theme_from_colors(theme_colors)
            return
        # Fallback: binary dark/light when no parent theme support
        dark_themes = [
            "Dark",
            "High Contrast Dark",
            "Nord",
            "Solarized Dark",
            "Dracula",
            "Monokai"
        ]
        light_themes = [
            "Light",
            "High Contrast Light",
            "Solarized Light"
        ]

        if theme in dark_themes:
            self.is_dark = True
        elif theme in light_themes:
            self.is_dark = False
        else:  # Auto (System) - detect system preference
            self.is_dark = _is_system_dark()
        
        # Apply stylesheet based on theme
        if self.is_dark:
            self.setStyleSheet("""
                QDialog {
                    background: #1E1E1E;
                }
                QWidget {
                    background: #1E1E1E;
                }
                QLineEdit, QComboBox, QSpinBox {
                    padding: 4px 8px;
                    border: 1px solid #48484A;
                    border-radius: 4px;
                    background: #3A3A3C;
                    color: #FFFFFF;
                }
                QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                    border: 2px solid #0A84FF;
                    padding: 3px 7px;
                }
                QComboBox::drop-down {
                    subcontrol-origin: padding;
                    subcontrol-position: top right;
                    width: 20px;
                    border-left: 1px solid #48484A;
                    background: #3A3A3C;
                }
                QComboBox::down-arrow {
                    image: none;
                    border-left: 4px solid transparent;
                    border-right: 4px solid transparent;
                    border-top: 5px solid #FFFFFF;
                    width: 0px;
                    height: 0px;
                    margin-right: 6px;
                }
                QComboBox QAbstractItemView {
                    background: #3A3A3C;
                    color: #FFFFFF;
                    border: 1px solid #48484A;
                    selection-background-color: #0A84FF;
                    selection-color: #FFFFFF;
                    outline: none;
                }
                QGroupBox {
                    font-weight: 600;
                    background: #2C2C2E;
                    border: 1px solid #3A3A3C;
                    color: #FFFFFF;
                }
                QLabel {
                    color: #FFFFFF;
                    background: transparent;
                }
                QFormLayout QLabel {
                    color: #FFFFFF;
                }
                QPushButton {
                    padding: 6px 16px;
                    border: none;
                    border-radius: 4px;
                    background: #0A84FF;
                    color: white;
                    font-weight: 500;
                }
                QPushButton:hover {
                    background: #0070E0;
                }
                QPushButton:pressed {
                    background: #0051D5;
                }
                QTabWidget::pane {
                    border: none;
                    background: #1E1E1E;
                }
                QTabBar::tab {
                    padding: 12px 14px;
                    min-width: 56px;
                    border: none;
                    background: transparent;
                    color: #8E8E93;
                    font-size: 15px;
                    font-weight: bold;
                }
                QTabBar::tab:selected {
                    color: #0A84FF;
                    border-right: 3px solid #0A84FF;
                }
                QTabBar::tab:hover:!selected {
                    background: rgba(10,132,255,0.08);
                }
                QCheckBox {
                    color: #FFFFFF;
                }
                QCheckBox::indicator {
                    border: 1px solid #48484A;
                    background: #3A3A3C;
                    border-radius: 3px;
                }
                QCheckBox::indicator:checked {
                    background: #0A84FF;
                    border: 1px solid #0A84FF;
                }
                QScrollArea {
                    border: none;
                    background: #1E1E1E;
                }
                QScrollArea > QWidget > QWidget {
                    background: #1E1E1E;
                }
            """)
        else:
            self.setStyleSheet("""
                QDialog {
                    background: #FFFFFF;
                }
                QLineEdit, QComboBox, QSpinBox {
                    padding: 4px 8px;
                    border: 1px solid #D1D1D6;
                    border-radius: 4px;
                    background: #FFFFFF;
                }
                QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                    border: 2px solid #007AFF;
                    padding: 3px 7px;
                }
                QComboBox::drop-down {
                    subcontrol-origin: padding;
                    subcontrol-position: top right;
                    width: 20px;
                    border-left: 1px solid #D1D1D6;
                    background: #FFFFFF;
                }
                QComboBox::down-arrow {
                    image: none;
                    border-left: 4px solid transparent;
                    border-right: 4px solid transparent;
                    border-top: 5px solid #1C1C1E;
                    width: 0px;
                    height: 0px;
                    margin-right: 6px;
                }
                QComboBox QAbstractItemView {
                    background: #FFFFFF;
                    color: #1C1C1E;
                    border: 1px solid #D1D1D6;
                    selection-background-color: #007AFF;
                    selection-color: #FFFFFF;
                    outline: none;
                }
                QGroupBox {
                    font-weight: 600;
                }
                QLabel {
                    color: #1C1C1E;
                }
                QPushButton {
                    padding: 6px 16px;
                    border: none;
                    border-radius: 4px;
                    background: #007AFF;
                    color: white;
                    font-weight: 500;
                }
                QPushButton:hover {
                    background: #0051D5;
                }
                QPushButton:pressed {
                    background: #003BB3;
                }
                QTabWidget::pane {
                    border: none;
                }
                QTabBar::tab {
                    padding: 12px 14px;
                    min-width: 56px;
                    border: none;
                    background: transparent;
                    color: #3C3C43;
                    font-size: 15px;
                    font-weight: bold;
                }
                QTabBar::tab:selected {
                    color: #007AFF;
                    border-right: 3px solid #007AFF;
                }
                QTabBar::tab:hover:!selected {
                    background: rgba(0,122,255,0.06);
                }
            """)
        
        # Refresh all group boxes to apply new theme
        self.update_group_styles()
        
        # Update header, tab bar, and button bar
        if self.is_dark:
            self.header.setStyleSheet("background: #2D2D2D; border-bottom: 1px solid #3A3A3C;")
            self.title_label.setStyleSheet("color: #FFFFFF;")
            self.tab_bar.setStyleSheet("background: #2D2D2D; border-bottom: 1px solid #3A3A3C;")
            self.btn_bar.setStyleSheet("background: #2D2D2D; border-top: 1px solid #3A3A3C;")
            if hasattr(self, "_tab_buttons"):
                for btn in self._tab_buttons:
                    btn.setStyleSheet(self._tab_btn_style_dark)
            self.close_btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    color: #0A84FF;
                    border: 1px solid #48484A;
                }
                QPushButton:hover {
                    background: rgba(10,132,255,0.1);
                }
            """)
        else:
            self.header.setStyleSheet("background: #F5F5F7; border-bottom: 1px solid #E5E5EA;")
            self.title_label.setStyleSheet("color: #1C1C1E;")
            self.tab_bar.setStyleSheet("background: #F5F5F7; border-bottom: 1px solid #E5E5EA;")
            self.btn_bar.setStyleSheet("background: #F5F5F7; border-top: 1px solid #E5E5EA;")
            if hasattr(self, "_tab_buttons"):
                for btn in self._tab_buttons:
                    btn.setStyleSheet(self._tab_btn_style_light)
            self.close_btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    color: #007AFF;
                    border: 1px solid #C7C7CC;
                }
                QPushButton:hover {
                    background: rgba(0,122,255,0.1);
                }
            """)
    
    def update_group_styles(self):
        """Update all group box styles when theme changes"""
        for tab in [self.general_tab, self.llm_tab, self.telegram_tab,
                    self.security_tab, self.integrations_tab, self.daemon_tab,
                    self.appearance_tab]:
            if hasattr(tab, 'get_group_style'):
                # Find all group boxes in the tab
                for child in tab.findChildren(QGroupBox):
                    child.setStyleSheet(tab.get_group_style())

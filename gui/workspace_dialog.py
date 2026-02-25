"""Workspace management dialog"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QMessageBox, QLineEdit,
    QTextEdit, QComboBox, QGroupBox, QFormLayout, QSpinBox,
    QDoubleSpinBox, QCheckBox, QTabWidget, QWidget, QFrame,
    QApplication, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QFont, QColor
import asyncio

from grizzyclaw.workspaces import WorkspaceManager, Workspace, WorkspaceConfig, WORKSPACE_TEMPLATES
from grizzyclaw.llm.lmstudio import _normalize_lmstudio_url


class WorkspaceDialog(QDialog):
    """Dialog for managing workspaces"""
    
    workspace_changed = pyqtSignal(str)  # Emitted when active workspace changes
    workspace_config_saved = pyqtSignal(str)  # Emitted when a workspace's config is saved (so chat can use new provider)
    
    def __init__(self, workspace_manager: WorkspaceManager, parent=None, llm_router=None):
        super().__init__(parent)
        self.manager = workspace_manager
        self._llm_router = llm_router
        self.is_dark = False
        self.setWindowTitle("🗂️ Workspaces")
        self.setMinimumSize(800, 600)
        
        # Get theme from parent (main window)
        if parent and hasattr(parent, 'settings'):
            theme = getattr(parent.settings, 'theme', 'Light')
            self.is_dark = theme in ['Dark', 'High Contrast Dark', 'Dracula', 'Monokai', 'Nord', 'Solarized Dark']
        
        self.setup_ui()
        self.refresh_list()
        self.workspace_list.model().rowsMoved.connect(self.on_rows_reordered)
    
    def setup_ui(self):
        # Theme colors
        if self.is_dark:
            self.bg_color = '#1E1E1E'
            self.fg_color = '#FFFFFF'
            self.sidebar_bg = '#2D2D2D'
            self.border_color = '#3A3A3C'
            self.input_bg = '#3A3A3C'
            self.accent_color = '#0A84FF'
            self.hover_bg = 'rgba(255, 255, 255, 0.05)'
        else:
            self.bg_color = '#FFFFFF'
            self.fg_color = '#1C1C1E'
            self.sidebar_bg = '#F5F5F7'
            self.border_color = '#E5E5EA'
            self.input_bg = '#FFFFFF'
            self.accent_color = '#007AFF'
            self.hover_bg = 'rgba(0, 0, 0, 0.05)'
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Left panel - workspace list
        self.left_panel = QWidget()
        self.left_panel.setFixedWidth(280)
        self.left_panel.setStyleSheet(f"background-color: {self.sidebar_bg}; border-right: 1px solid {self.border_color};")
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(16, 20, 16, 16)
        left_layout.setSpacing(12)
        
        # Header
        header = QLabel("Workspaces")
        header.setFont(QFont("-apple-system", 18, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {self.fg_color}; background: transparent;")
        left_layout.addWidget(header)
        
        # Workspace list
        self.workspace_list = QListWidget()
        self.workspace_list.setStyleSheet(f"""
            QListWidget {{
                border: none;
                background: transparent;
                outline: none;
                color: {self.fg_color};
            }}
            QListWidget::item {{
                padding: 12px;
                border-radius: 8px;
                margin: 2px 0;
                color: {self.fg_color};
            }}
            QListWidget::item:selected {{
                background-color: {self.accent_color};
                color: white;
            }}
            QListWidget::item:hover:!selected {{
                background-color: {self.hover_bg};
            }}
        """)
        self.workspace_list.currentItemChanged.connect(self.on_workspace_selected)
        self.workspace_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        left_layout.addWidget(self.workspace_list, 1)
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        
        self.add_btn = QPushButton("+ New")
        self.add_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #0056CC;
            }}
        """)
        self.add_btn.clicked.connect(self.create_workspace)
        btn_layout.addWidget(self.add_btn)
        
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setStyleSheet("""
            QPushButton {
                background-color: #FF3B30;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
            }
            QPushButton:hover {
                background-color: #D32F2F;
            }
        """)
        self.delete_btn.clicked.connect(self.delete_workspace)
        btn_layout.addWidget(self.delete_btn)
        
        left_layout.addLayout(btn_layout)
        layout.addWidget(self.left_panel)
        
        # Right panel - workspace details
        self.right_panel = QWidget()
        self.right_panel.setStyleSheet(f"background-color: {self.bg_color};")
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(24, 20, 24, 20)
        right_layout.setSpacing(16)
        
        # Workspace name header
        self.name_label = QLabel("Select a workspace")
        self.name_label.setFont(QFont("-apple-system", 24, QFont.Weight.Bold))
        self.name_label.setStyleSheet(f"color: {self.fg_color};")
        right_layout.addWidget(self.name_label)
        
        # Tabs for different settings
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {self.border_color};
                border-radius: 8px;
                background: {self.bg_color};
            }}
            QTabWidget::tab-bar {{
                alignment: left;
            }}
            QTabBar::tab {{
                padding: 8px 16px;
                margin-right: 4px;
                border: none;
                background: transparent;
                color: {self.fg_color};
            }}
            QTabBar::tab:selected {{
                background: {self.accent_color};
                color: white;
                border-radius: 6px;
            }}
        """)
        
        # General tab
        general_tab = QWidget()
        general_layout = QFormLayout(general_tab)
        general_layout.setContentsMargins(16, 16, 16, 16)
        general_layout.setSpacing(12)
        
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Workspace name")
        general_layout.addRow("Name:", self.name_input)
        
        self.desc_input = QTextEdit()
        self.desc_input.setMaximumHeight(80)
        self.desc_input.setPlaceholderText("Description")
        general_layout.addRow("Description:", self.desc_input)
        
        icon_row = QHBoxLayout()
        self.icon_input = QLineEdit()
        self.icon_input.setMaximumWidth(50)
        self.icon_input.setPlaceholderText("🤖")
        icon_row.addWidget(self.icon_input)
        
        self.color_input = QLineEdit()
        self.color_input.setMaximumWidth(100)
        self.color_input.setPlaceholderText("#007AFF")
        icon_row.addWidget(QLabel("Color:"))
        icon_row.addWidget(self.color_input)
        icon_row.addStretch()
        general_layout.addRow("Icon:", icon_row)
        
        avatar_row = QHBoxLayout()
        self.avatar_path_input = QLineEdit()
        self.avatar_path_input.setPlaceholderText("Path or URL to custom/VL-generated avatar image (optional)")
        avatar_row.addWidget(self.avatar_path_input)
        avatar_browse_btn = QPushButton("Browse…")
        avatar_browse_btn.setFixedWidth(80)
        avatar_browse_btn.clicked.connect(self._browse_avatar_path)
        avatar_row.addWidget(avatar_browse_btn)
        general_layout.addRow("Avatar:", avatar_row)
        
        self.tabs.addTab(general_tab, "General")
        
        # LLM tab
        llm_tab = QWidget()
        llm_layout = QFormLayout(llm_tab)
        llm_layout.setContentsMargins(16, 16, 16, 16)
        llm_layout.setSpacing(12)
        
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["ollama", "lmstudio", "openai", "anthropic", "openrouter"])
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        llm_layout.addRow("Provider:", self.provider_combo)
        
        # Model selection row with combo and refresh button
        model_row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)  # Allow custom model entry
        self.model_combo.setPlaceholderText("Select or type model name")
        self.model_combo.setMinimumWidth(200)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        model_row.addWidget(self.model_combo)
        
        self.refresh_models_btn = QPushButton("🔄")
        self.refresh_models_btn.setFixedSize(32, 32)
        self.refresh_models_btn.setToolTip("Refresh models from provider")
        self.refresh_models_btn.clicked.connect(self._refresh_models)
        model_row.addWidget(self.refresh_models_btn)
        model_row.addStretch()
        llm_layout.addRow("Model:", model_row)
        
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.1)
        self.temp_spin.setValue(0.7)
        llm_layout.addRow("Temperature:", self.temp_spin)
        
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(100, 131072)
        self.max_tokens_spin.setValue(131072)
        llm_layout.addRow("Max Tokens:", self.max_tokens_spin)

        self.model_max_context_lbl = QLabel("")
        self.model_max_context_lbl.setStyleSheet("color: gray; font-size: 11px;")
        self.model_max_context_lbl.setWordWrap(True)
        llm_layout.addRow(self.model_max_context_lbl)

        self.max_session_spin = QSpinBox()
        self.max_session_spin.setRange(4, 100)
        self.max_session_spin.setValue(20)
        self.max_session_spin.setToolTip("Max conversation turns to keep. Older tool-heavy messages are prioritized.")
        llm_layout.addRow("Context Window (messages):", self.max_session_spin)

        self.use_agents_sdk_cb = QCheckBox("Use Agents SDK (OpenAI + LiteLLM)")
        self.use_agents_sdk_cb.setToolTip(
            "Use OpenAI Agents SDK with LiteLLM for improved coding workflows. "
            "Requires: pip install 'openai-agents[litellm]'. Uses MCP tools natively."
        )
        llm_layout.addRow(self.use_agents_sdk_cb)

        self.max_turns_spin = QSpinBox()
        self.max_turns_spin.setRange(5, 100)
        self.max_turns_spin.setValue(25)
        self.max_turns_spin.setToolTip(
            "Max agent turns when using Agents SDK (tool-call iterations). "
            "Increase for complex multi-file coding tasks."
        )
        llm_layout.addRow("Agents SDK Max Turns:", self.max_turns_spin)

        self.tabs.addTab(llm_tab, "LLM")
        
        # Prompt tab
        prompt_tab = QWidget()
        prompt_layout = QVBoxLayout(prompt_tab)
        prompt_layout.setContentsMargins(16, 16, 16, 16)
        
        prompt_layout.addWidget(QLabel("System Prompt:"))
        self.prompt_input = QTextEdit()
        self.prompt_input.setPlaceholderText("Enter the system prompt for this workspace...")
        prompt_layout.addWidget(self.prompt_input)
        
        self.tabs.addTab(prompt_tab, "Prompt")
        
        # API Keys tab
        api_tab = QWidget()
        api_layout = QFormLayout(api_tab)
        api_layout.setContentsMargins(16, 16, 16, 16)
        api_layout.setSpacing(12)
        
        self.openai_key_input = QLineEdit()
        self.openai_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key_input.setPlaceholderText("Leave empty to use global setting")
        api_layout.addRow("OpenAI Key:", self.openai_key_input)
        
        self.anthropic_key_input = QLineEdit()
        self.anthropic_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.anthropic_key_input.setPlaceholderText("Leave empty to use global setting")
        api_layout.addRow("Anthropic Key:", self.anthropic_key_input)
        
        self.openrouter_key_input = QLineEdit()
        self.openrouter_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.openrouter_key_input.setPlaceholderText("Leave empty to use global setting")
        api_layout.addRow("OpenRouter Key:", self.openrouter_key_input)
        
        self.tabs.addTab(api_tab, "API Keys")

        # Swarm / Inter-agent tab
        swarm_tab = QWidget()
        swarm_layout = QFormLayout(swarm_tab)
        swarm_layout.setContentsMargins(16, 16, 16, 16)
        swarm_layout.setSpacing(12)
        self.enable_inter_agent_cb = QCheckBox("Allow this workspace to receive and send messages to other agents (@mentions)")
        self.enable_inter_agent_cb.setToolTip("Enable agent-to-agent chat: type @workspace_name or @slug to delegate (e.g. @code_assistant analyze this).")
        swarm_layout.addRow(self.enable_inter_agent_cb)
        self.inter_agent_channel_input = QLineEdit()
        self.inter_agent_channel_input.setPlaceholderText("Optional: e.g. swarm1 (only same-channel workspaces can message each other)")
        swarm_layout.addRow("Inter-agent channel:", self.inter_agent_channel_input)
        self.use_shared_memory_cb = QCheckBox("Use shared memory with other agents in the same channel")
        self.use_shared_memory_cb.setToolTip("When enabled, this workspace shares a memory DB with other workspaces on the same channel for swarm context.")
        swarm_layout.addRow(self.use_shared_memory_cb)
        self.swarm_auto_delegate_cb = QCheckBox("Leader: auto-run @mentions from my response (break task → delegate to specialists)")
        self.swarm_auto_delegate_cb.setToolTip("When this workspace is the leader, any @research / @coding / @personal / @planning lines in its reply are executed and specialist replies are collected.")
        swarm_layout.addRow(self.swarm_auto_delegate_cb)
        self.swarm_consensus_cb = QCheckBox("Leader: synthesize specialist replies into one consensus answer")
        self.swarm_consensus_cb.setToolTip("After delegations, call the leader again to combine specialist responses into a single recommendation.")
        swarm_layout.addRow(self.swarm_consensus_cb)
        # Proactivity (memuBot-style)
        proact_group = QGroupBox("Proactivity")
        proact_layout = QFormLayout(proact_group)
        self.proactive_habits_cb = QCheckBox("Habit learning: analyze memory and auto-schedule actions (e.g. prep env Mon–Fri)")
        self.proactive_habits_cb.setToolTip("Daily job analyzes memory patterns and suggests habit-based reminders.")
        proact_layout.addRow(self.proactive_habits_cb)
        self.proactive_screen_cb = QCheckBox("Screen awareness: periodic screenshot + VL analysis for desktop context")
        self.proactive_screen_cb.setToolTip("Every 30 min, capture screen and ask the model what the user is doing; store summary in memory.")
        proact_layout.addRow(self.proactive_screen_cb)
        self.proactive_autonomy_cb = QCheckBox("Continuous Autonomy: background loop for predictive prep and tasks")
        self.proactive_autonomy_cb.setToolTip("Agent creates a background loop checking workspace state periodically even without prompts.")
        proact_layout.addRow(self.proactive_autonomy_cb)
        self.proactive_autonomy_interval_spin = QSpinBox()
        self.proactive_autonomy_interval_spin.setRange(5, 60)
        self.proactive_autonomy_interval_spin.setSuffix(" min")
        self.proactive_autonomy_interval_spin.setValue(15)
        self.proactive_autonomy_interval_spin.setToolTip("How often the autonomy loop runs (5–60 minutes).")
        proact_layout.addRow("Autonomy interval:", self.proactive_autonomy_interval_spin)
        self.proactive_file_triggers_cb = QCheckBox("Triggers on file changes and Git events")
        self.proactive_file_triggers_cb.setToolTip("Watch ~/.grizzyclaw/file_watcher.json for watch_dirs; triggers.json can use event file_change or git_event.")
        proact_layout.addRow(self.proactive_file_triggers_cb)
        swarm_layout.addRow(proact_group)
        swarm_hint = QLabel("Tip: In chat, use @workspace_slug or @Workspace Name to delegate (e.g. @coding analyze this code). Leader can output @research / @coding / @personal / @planning to auto-delegate.")
        swarm_hint.setWordWrap(True)
        swarm_hint.setStyleSheet("color: gray; font-size: 11px;")
        swarm_layout.addRow(swarm_hint)
        self.tabs.addTab(swarm_tab, "Swarm")

        # Metrics tab
        metrics_tab = QWidget()
        metrics_layout = QVBoxLayout(metrics_tab)
        metrics_layout.setContentsMargins(16, 16, 16, 16)
        
        metrics_group = QGroupBox("📊 Performance Metrics")
        metrics_form = QFormLayout(metrics_group)
        metrics_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        
        self.avg_time_lbl = QLabel("0.0 ms")
        metrics_form.addRow("Avg Response Time:", self.avg_time_lbl)
        
        self.total_tokens_lbl = QLabel("0")
        metrics_form.addRow("Total Tokens:", self.total_tokens_lbl)
        
        self.quality_lbl = QLabel("0.0%")
        metrics_form.addRow("Quality Score:", self.quality_lbl)
        
        self.messages_lbl = QLabel("0")
        metrics_form.addRow("Messages:", self.messages_lbl)
        
        self.session_lbl = QLabel("0")
        metrics_form.addRow("Sessions:", self.session_lbl)
        
        metrics_layout.addWidget(metrics_group)
        
        self.benchmark_btn = QPushButton("🚀 Run Benchmark (5 prompts)")
        self.benchmark_btn.setObjectName("benchmark_btn")
        self.benchmark_btn.clicked.connect(self.run_benchmark)
        metrics_layout.addWidget(self.benchmark_btn)
        
        self.tabs.addTab(metrics_tab, "Metrics")
        
        right_layout.addWidget(self.tabs, 1)
        
        # Action buttons
        action_layout = QHBoxLayout()
        action_layout.setSpacing(12)
        
        self.switch_btn = QPushButton("🔄 Switch to This Workspace")
        self.switch_btn.setStyleSheet("""
            QPushButton {
                background-color: #34C759;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 12px 24px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2CA048;
            }
        """)
        self.switch_btn.clicked.connect(self.switch_workspace)
        action_layout.addWidget(self.switch_btn)
        
        self.save_btn = QPushButton("💾 Save Changes")
        self.save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 12px 24px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #0056CC;
            }}
        """)
        self.save_btn.clicked.connect(self.save_workspace)
        action_layout.addWidget(self.save_btn)
        
        action_layout.addStretch()
        
        self.duplicate_btn = QPushButton("📋 Duplicate")
        self.duplicate_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.sidebar_bg};
                color: {self.fg_color};
                border: 1px solid {self.border_color};
                border-radius: 6px;
                padding: 12px 24px;
            }}
            QPushButton:hover {{
                background-color: {self.hover_bg};
            }}
        """)
        self.duplicate_btn.clicked.connect(self.duplicate_workspace)
        action_layout.addWidget(self.duplicate_btn)
        
        right_layout.addLayout(action_layout)
        
        layout.addWidget(self.right_panel, 1)
        
        # Apply theme to inputs
        self._apply_input_styles()
    
    def _apply_input_styles(self):
        """Apply theme styles to all input widgets"""
        input_style = f"""
            QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
                background-color: {self.input_bg};
                color: {self.fg_color};
                border: 1px solid {self.border_color};
                border-radius: 4px;
                padding: 6px 8px;
            }}
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
                border: 2px solid {self.accent_color};
            }}
            QLabel {{
                color: {self.fg_color};
                background: transparent;
            }}
            QCheckBox {{
                color: {self.fg_color};
            }}
            QGroupBox {{
                color: {self.fg_color};
                border: 1px solid {self.border_color};
                border-radius: 6px;
                margin-top: 8px;
                padding: 8px 16px 16px 16px;
                background: {self.sidebar_bg};
            }}
            QGroupBox::title {{
                color: {self.fg_color};
            }}
        """
        self.right_panel.setStyleSheet(f"background-color: {self.bg_color}; " + input_style)

    def refresh_list(self, select_workspace_id: str = None):
        """Refresh the workspace list
        
        Args:
            select_workspace_id: Workspace ID to re-select after refresh.
                                 Defaults to the active workspace.
        """
        # Remember which workspace to re-select
        target_id = select_workspace_id or self.get_selected_workspace_id() or self.manager.active_workspace_id
        active_id = self.manager.active_workspace_id
        
        # Block signals while rebuilding to avoid triggering on_workspace_selected
        # multiple times during list rebuild
        self.workspace_list.blockSignals(True)
        self.workspace_list.clear()
        
        target_item = None
        for workspace in self.manager.list_workspaces():
            item_text = f"{workspace.icon} {workspace.name}"
            if workspace.id == active_id:
                item_text += " ✓"
            
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, workspace.id)
            self.workspace_list.addItem(item)
            
            if workspace.id == target_id:
                target_item = item
        
        self.workspace_list.blockSignals(False)
        
        # Now select the target workspace (triggers on_workspace_selected once)
        if target_item:
            self.workspace_list.setCurrentItem(target_item)
    
    def on_workspace_selected(self, current, previous):
        """Handle workspace selection"""
        if not current:
            return
        
        workspace_id = current.data(Qt.ItemDataRole.UserRole)
        workspace = self.manager.get_workspace(workspace_id)
        if not workspace:
            return
        
        # Update header
        self.name_label.setText(f"{workspace.icon} {workspace.name}")
        
        # General tab
        self.name_input.setText(workspace.name)
        self.desc_input.setText(workspace.description)
        self.icon_input.setText(workspace.icon)
        self.color_input.setText(workspace.color)
        self.avatar_path_input.setText(workspace.avatar_path or "")
        
        # LLM tab — block provider_combo signals so _on_provider_changed
        # does NOT fire during form population (it would clear model_combo
        # and cause unnecessary side effects)
        self.provider_combo.blockSignals(True)
        provider_idx = self.provider_combo.findText(workspace.config.llm_provider)
        if provider_idx >= 0:
            self.provider_combo.setCurrentIndex(provider_idx)
        self.provider_combo.blockSignals(False)
        
        # Manually refresh model defaults for the provider (since we blocked the signal)
        self._on_provider_changed(workspace.config.llm_provider)
        
        # Set model in combo box
        model_text = workspace.config.llm_model
        idx = self.model_combo.findText(model_text)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        else:
            self.model_combo.setCurrentText(model_text)
        self.temp_spin.setValue(workspace.config.temperature)
        self.max_tokens_spin.setValue(workspace.config.max_tokens)
        self.max_session_spin.setValue(getattr(workspace.config, "max_session_messages", 20))
        self.use_agents_sdk_cb.setChecked(getattr(workspace.config, "use_agents_sdk", False))
        self.max_turns_spin.setValue(getattr(workspace.config, "agents_sdk_max_turns", 25))
        self._update_max_tokens_from_model()

        # Prompt tab
        self.prompt_input.setText(workspace.config.system_prompt)
        
        # API Keys tab
        self.openai_key_input.setText(workspace.config.openai_api_key or "")
        self.anthropic_key_input.setText(workspace.config.anthropic_api_key or "")
        self.openrouter_key_input.setText(workspace.config.openrouter_api_key or "")
        
        # Swarm tab
        self.enable_inter_agent_cb.setChecked(getattr(workspace.config, "enable_inter_agent", False))
        self.inter_agent_channel_input.setText((workspace.config.inter_agent_channel or "").strip())
        self.use_shared_memory_cb.setChecked(getattr(workspace.config, "use_shared_memory", False))
        self.swarm_auto_delegate_cb.setChecked(getattr(workspace.config, "swarm_auto_delegate", False))
        self.swarm_consensus_cb.setChecked(getattr(workspace.config, "swarm_consensus", False))
        self.proactive_habits_cb.setChecked(getattr(workspace.config, "proactive_habits", False))
        self.proactive_screen_cb.setChecked(getattr(workspace.config, "proactive_screen", False))
        self.proactive_autonomy_cb.setChecked(getattr(workspace.config, "proactive_autonomy", False))
        self.proactive_autonomy_interval_spin.setValue(max(5, min(60, getattr(workspace.config, "proactive_autonomy_interval_minutes", 15) or 15)))
        self.proactive_file_triggers_cb.setChecked(getattr(workspace.config, "proactive_file_triggers", False))
        
        # Metrics tab
        self.avg_time_lbl.setText(f"{workspace.avg_response_time_ms:.1f} ms")
        self.total_tokens_lbl.setText(f"{workspace.total_tokens:,}")
        total_fb = workspace.feedback_up + workspace.feedback_down
        self.quality_lbl.setText(
            f"{workspace.quality_score:.1f}%" if total_fb > 0 else "N/A (no feedback yet)"
        )
        self.messages_lbl.setText(str(workspace.message_count))
        self.session_lbl.setText(str(workspace.session_count))
        
        # Update button state
        is_active = workspace.id == self.manager.active_workspace_id
        self.switch_btn.setEnabled(not is_active)
        self.switch_btn.setText("✓ Active Workspace" if is_active else "🔄 Switch to This Workspace")
        self.delete_btn.setEnabled(not workspace.is_default)

    def _browse_avatar_path(self):
        """Open file dialog to pick an avatar image and set the Avatar path."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose avatar image",
            self.avatar_path_input.text().strip() or "",
            "Images (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;All files (*)",
        )
        if path:
            self.avatar_path_input.setText(path)
    
    def get_selected_workspace_id(self) -> str:
        """Get the currently selected workspace ID"""
        current = self.workspace_list.currentItem()
        if current:
            return current.data(Qt.ItemDataRole.UserRole)
        return None
    
    def create_workspace(self):
        """Create a new workspace"""
        # Show template selection dialog
        dialog = TemplateDialog(self)
        if dialog.exec():
            template = dialog.selected_template
            name = dialog.name_input.text() or f"New Workspace"
            
            workspace = self.manager.create_workspace(
                name=name,
                template=template
            )
            self.refresh_list()
            
            # Select the new workspace
            for i in range(self.workspace_list.count()):
                item = self.workspace_list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == workspace.id:
                    self.workspace_list.setCurrentItem(item)
                    break
    
    def save_workspace(self):
        """Save workspace changes"""
        workspace_id = self.get_selected_workspace_id()
        if not workspace_id:
            return
        
        # Flush any pending UI events so that checkbox / widget states
        # are fully up-to-date before we read them.  Without this,
        # rapidly clicking a checkbox then Save can read stale isChecked()
        # values because the checkbox click event hasn't propagated yet.
        QApplication.processEvents()
        
        # Collect ALL values from the form FIRST, before any save calls
        # that might trigger signals / refresh_list / on_workspace_selected
        # and overwrite widget state.
        name = self.name_input.text()
        description = self.desc_input.toPlainText()
        icon = self.icon_input.text() or "🤖"
        color = self.color_input.text() or "#007AFF"
        avatar_path = self.avatar_path_input.text().strip() or None
        
        config_updates = {
            "llm_provider": self.provider_combo.currentText(),
            "llm_model": self.model_combo.currentText(),
            "temperature": self.temp_spin.value(),
            "max_tokens": self.max_tokens_spin.value(),
            "max_session_messages": self.max_session_spin.value(),
            "use_agents_sdk": self.use_agents_sdk_cb.isChecked(),
            "agents_sdk_max_turns": self.max_turns_spin.value(),
            "system_prompt": self.prompt_input.toPlainText(),
            "openai_api_key": self.openai_key_input.text() or None,
            "anthropic_api_key": self.anthropic_key_input.text() or None,
            "openrouter_api_key": self.openrouter_key_input.text() or None,
            "enable_inter_agent": self.enable_inter_agent_cb.isChecked(),
            "inter_agent_channel": self.inter_agent_channel_input.text().strip() or None,
            "use_shared_memory": self.use_shared_memory_cb.isChecked(),
            "swarm_auto_delegate": self.swarm_auto_delegate_cb.isChecked(),
            "swarm_consensus": self.swarm_consensus_cb.isChecked(),
            "proactive_habits": self.proactive_habits_cb.isChecked(),
            "proactive_screen": self.proactive_screen_cb.isChecked(),
            "proactive_autonomy": self.proactive_autonomy_cb.isChecked(),
            "proactive_autonomy_interval_minutes": self.proactive_autonomy_interval_spin.value(),
            "proactive_file_triggers": self.proactive_file_triggers_cb.isChecked(),
        }
        
        # Now perform the saves using the captured values
        self.manager.update_workspace(
            workspace_id,
            name=name,
            description=description,
            icon=icon,
            color=color,
            avatar_path=avatar_path,
        )
        
        self.manager.update_workspace_config(workspace_id, config_updates)
        
        # Re-select the same workspace (not just the active one)
        self.refresh_list(select_workspace_id=workspace_id)
        self.workspace_config_saved.emit(workspace_id)
        QMessageBox.information(self, "Saved", "Workspace saved successfully.")
    
    def switch_workspace(self):
        """Switch to the selected workspace"""
        workspace_id = self.get_selected_workspace_id()
        if workspace_id and self.manager.set_active_workspace(workspace_id):
            self.workspace_changed.emit(workspace_id)
            self.refresh_list()
            self.on_workspace_selected(self.workspace_list.currentItem(), None)
    
    def delete_workspace(self):
        """Delete the selected workspace"""
        workspace_id = self.get_selected_workspace_id()
        if not workspace_id:
            return
        
        workspace = self.manager.get_workspace(workspace_id)
        if workspace.is_default:
            QMessageBox.warning(self, "Cannot Delete", "Cannot delete the default workspace.")
            return
        
        reply = QMessageBox.question(
            self, 
            "Delete Workspace",
            f"Delete workspace '{workspace.name}'?\nThis cannot be undone."
        )
        if reply == QMessageBox.StandardButton.Yes:
            if self.manager.delete_workspace(workspace_id):
                self.refresh_list()
    
    def run_benchmark(self):
        """Run benchmark test: 5 short prompts, report latencies and compare providers."""
        router = getattr(self, "_llm_router", None)
        if not router or not getattr(router, "providers", None):
            QMessageBox.warning(
                self, "Benchmark",
                "No LLM router available. Open Workspaces from the main window after chat is ready."
            )
            return
        btn = getattr(self, "benchmark_btn", None)
        if btn:
            btn.setEnabled(False)
            btn.setText("Running…")

        class BenchmarkWorker(QThread):
            result_ready = pyqtSignal(list, float, str)  # (list of (prompt, sec), total_sec, error_msg)

            def __init__(self, router_ref):
                super().__init__()
                self.router_ref = router_ref

            def run(self):
                import time
                import asyncio
                prompts = [
                    "Reply with exactly: OK",
                    "What is 2+2? Answer in one number.",
                    "Say hello in one word.",
                    "Name one fruit.",
                    "Reply with the word done.",
                ]
                results = []
                total = 0.0
                err_msg = ""
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        for p in prompts:
                            t0 = time.perf_counter()
                            try:
                                buf = []
                                async def consume():
                                    async for ch in self.router_ref.generate(
                                        [{"role": "user", "content": p}],
                                        max_tokens=50,
                                    ):
                                        buf.append(ch)
                                loop.run_until_complete(consume())
                                elapsed = time.perf_counter() - t0
                                results.append((p[:40] + ("…" if len(p) > 40 else ""), round(elapsed, 2)))
                                total += elapsed
                            except Exception as e:
                                err_msg = str(e)
                                results.append((p[:40] + "…", f"Error: {e}"))
                                break
                    finally:
                        loop.close()
                except Exception as e:
                    err_msg = str(e)
                self.result_ready.emit(results, total, err_msg)

        def on_done(results, total, err_msg):
            btn = getattr(self, "benchmark_btn", None)
            if btn:
                btn.setEnabled(True)
                btn.setText("🚀 Run Benchmark (5 prompts)")
            lines = []
            for prompt, sec in results:
                lines.append(f"• {prompt}: {sec}" + ("s" if isinstance(sec, (int, float)) else ""))
            if total and not err_msg:
                lines.append(f"\nTotal: {round(total, 2)}s")
            msg = "\n".join(lines) if lines else "No runs completed."
            if err_msg:
                msg += f"\n\nError: {err_msg}"
            QMessageBox.information(self, "Benchmark", msg)

        worker = BenchmarkWorker(router)
        worker.result_ready.connect(on_done)
        worker.start()
        worker.finished.connect(lambda: worker.deleteLater())
    
    def duplicate_workspace(self):
        """Duplicate the selected workspace"""
        workspace_id = self.get_selected_workspace_id()
        if not workspace_id:
            return
        
        workspace = self.manager.get_workspace(workspace_id)
        new_workspace = self.manager.duplicate_workspace(
            workspace_id,
            f"{workspace.name} (Copy)"
        )
        if new_workspace:
            self.refresh_list()
    
    def _on_provider_changed(self, provider: str):
        """Handle provider change - refresh model list"""
        self.model_combo.clear()
        # Add some default models based on provider
        default_models = {
            "ollama": ["llama3.2", "llama3.1", "mistral", "codellama", "phi3"],
            "lmstudio": [],  # Will be populated by refresh
            "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
            "anthropic": ["claude-sonnet-4-5-20250929", "claude-opus-4-6", "claude-haiku-4-5-20251001"],
            "openrouter": ["openai/gpt-4o", "anthropic/claude-3.5-sonnet", "google/gemini-pro"],
        }
        if provider in default_models:
            self.model_combo.addItems(default_models[provider])
        self._update_max_tokens_from_model()
    
    def _refresh_models(self):
        """Fetch models from the selected provider"""
        provider = self.provider_combo.currentText()
        self.refresh_models_btn.setEnabled(False)
        self.refresh_models_btn.setText("...")
        
        try:
            if provider == "ollama":
                self._fetch_ollama_models()
            elif provider == "lmstudio":
                self._fetch_lmstudio_models()
            elif provider == "openai":
                self._fetch_openai_models()
            else:
                QMessageBox.information(self, "Info", f"Auto-fetch not available for {provider}.\nType the model name manually.")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to fetch models: {str(e)}")
        finally:
            self.refresh_models_btn.setEnabled(True)
            self.refresh_models_btn.setText("🔄")
    
    def _fetch_ollama_models(self):
        """Fetch models from Ollama using the same URL as in main Settings."""
        try:
            from grizzyclaw.llm.ollama import OllamaProvider
            url = "http://localhost:11434"
            parent = self.parent()
            if parent and hasattr(parent, "settings"):
                url = getattr(parent.settings, "ollama_url", url) or url
            provider = OllamaProvider(url)
            from grizzyclaw.utils.async_runner import run_async
            models_data = run_async(provider.list_models())
            models = [m["id"] if isinstance(m, dict) else str(m) for m in models_data]
            current = self.model_combo.currentText()
            self.model_combo.clear()
            self.model_combo.addItems(models)
            if current:
                idx = self.model_combo.findText(current)
                if idx >= 0:
                    self.model_combo.setCurrentIndex(idx)
                else:
                    self.model_combo.setCurrentText(current)
            self.model_combo.setFocus()
            self._update_max_tokens_from_model()
        except Exception as e:
            QMessageBox.warning(self, "Ollama Error", f"Could not fetch models from Ollama:\n{str(e)}\n\nMake sure Ollama is running.")
    
    def _fetch_lmstudio_models(self):
        """Fetch models from LM Studio using the same URL as in main Settings (or workspace config)."""
        try:
            from grizzyclaw.llm.lmstudio import LMStudioProvider
            # Use main window's settings so Workspace LLM uses same URL as Settings → LLM Providers
            url = "http://localhost:1234/v1"
            parent = self.parent()
            if parent and hasattr(parent, "settings"):
                url = getattr(parent.settings, "lmstudio_url", url) or url
            else:
                # Fallback: current workspace config if we have one
                ws_id = self.get_selected_workspace_id()
                if ws_id:
                    ws = self.manager.get_workspace(ws_id)
                    if ws:
                        url = ws.config.lmstudio_url or url
            url = _normalize_lmstudio_url(url)
            provider = LMStudioProvider(url)
            from grizzyclaw.utils.async_runner import run_async
            models_data = run_async(provider.list_models())
            models = [m["id"] if isinstance(m, dict) else str(m) for m in models_data]
            current = self.model_combo.currentText()
            self.model_combo.clear()
            self.model_combo.addItems(models)
            if current:
                idx = self.model_combo.findText(current)
                if idx >= 0:
                    self.model_combo.setCurrentIndex(idx)
                else:
                    self.model_combo.setCurrentText(current)
            self.model_combo.setFocus()
            self._update_max_tokens_from_model()
        except Exception as e:
            QMessageBox.warning(self, "LM Studio Error", f"Could not fetch models from LM Studio:\n{str(e)}\n\nUse Settings → LLM Providers to set the LM Studio URL, then try again.")
    
    def _on_model_changed(self):
        """When model changes, update max tokens spinbox ceiling from provider query (Ollama/LM Studio)."""
        self._update_max_tokens_from_model()

    def _update_max_tokens_from_model(self):
        """Query model's max context length (Ollama/LM Studio) and cap max_tokens spinbox."""
        provider = self.provider_combo.currentText()
        model = self.model_combo.currentText().strip()
        self.model_max_context_lbl.setText("")
        if provider not in ("ollama", "lmstudio") or not model:
            self.max_tokens_spin.setMaximum(131072)
            return
        try:
            from grizzyclaw.utils.async_runner import run_async
            parent = self.parent()
            if provider == "ollama":
                from grizzyclaw.llm.ollama import OllamaProvider
                url = "http://localhost:11434"
                if parent and hasattr(parent, "settings"):
                    url = getattr(parent.settings, "ollama_url", url) or url
                prov = OllamaProvider(url)
            else:
                from grizzyclaw.llm.lmstudio import LMStudioProvider
                url = "http://localhost:1234/v1"
                if parent and hasattr(parent, "settings"):
                    url = getattr(parent.settings, "lmstudio_url", url) or url
                url = _normalize_lmstudio_url(url)
                prov = LMStudioProvider(url)
            max_ctx = run_async(prov.get_model_context_length(model))
            ceiling = max_ctx if max_ctx and max_ctx > 0 else 131072
            self.max_tokens_spin.setMaximum(ceiling)
            if self.max_tokens_spin.value() > ceiling:
                self.max_tokens_spin.setValue(ceiling)
            if max_ctx and max_ctx > 0:
                self.model_max_context_lbl.setText(f"Model max context: {max_ctx:,} tokens")
            else:
                self.model_max_context_lbl.setText("Could not query model; using default max.")
        except Exception:
            self.max_tokens_spin.setMaximum(131072)
            self.model_max_context_lbl.setText("Could not query model; using default max.")

    def _fetch_openai_models(self):
        """Fetch models from OpenAI"""
        # OpenAI requires API key, show defaults
        self.model_combo.clear()
        self.model_combo.addItems(["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo", "gpt-4"])
        QMessageBox.information(self, "OpenAI", "Default OpenAI models loaded.\nType a custom model name if needed.")

    def on_rows_reordered(self, parent, start, end, destination):
        """Handle row reordering after drag-drop"""
        self._update_workspace_orders()

    def _update_workspace_orders(self):
        """Update workspace.order based on current list order"""
        for row in range(self.workspace_list.count()):
            item = self.workspace_list.item(row)
            ws_id = item.data(Qt.ItemDataRole.UserRole)
            ws = self.manager.workspaces.get(ws_id)
            if ws:
                ws.order = row
        self.manager._save_workspaces()
        if hasattr(self.parent(), 'sidebar') and self.parent():
            self.parent().sidebar.refresh_workspace_buttons()


class TemplateDialog(QDialog):
    """Dialog for selecting a workspace template"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_template = None
        self.is_dark = False
        
        # Get theme from parent (WorkspaceDialog)
        if parent and hasattr(parent, 'is_dark'):
            self.is_dark = parent.is_dark
        
        self.setWindowTitle("Create Workspace")
        self.setMinimumSize(500, 400)
        self.setup_ui()
    
    def setup_ui(self):
        # Theme colors
        if self.is_dark:
            self.bg_color = '#1E1E1E'
            self.fg_color = '#FFFFFF'
            self.border_color = '#3A3A3C'
            self.input_bg = '#3A3A3C'
            self.accent_color = '#0A84FF'
        else:
            self.bg_color = '#FFFFFF'
            self.fg_color = '#1C1C1E'
            self.border_color = '#E5E5EA'
            self.input_bg = '#FFFFFF'
            self.accent_color = '#007AFF'
        
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {self.bg_color};
            }}
            QLabel {{
                color: {self.fg_color};
            }}
            QLineEdit {{
                background-color: {self.input_bg};
                color: {self.fg_color};
                border: 1px solid {self.border_color};
                border-radius: 4px;
                padding: 6px 8px;
            }}
            QLineEdit:focus {{
                border: 2px solid {self.accent_color};
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)
        
        # Name input
        name_layout = QFormLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("My Workspace")
        name_layout.addRow("Name:", self.name_input)
        layout.addLayout(name_layout)
        
        # Template selection
        layout.addWidget(QLabel("Choose a template:"))
        
        self.template_list = QListWidget()
        self.template_list.setStyleSheet(f"""
            QListWidget {{
                border: 1px solid {self.border_color};
                border-radius: 8px;
                background: {self.bg_color};
                color: {self.fg_color};
            }}
            QListWidget::item {{
                padding: 12px;
                border-bottom: 1px solid {self.border_color};
                color: {self.fg_color};
            }}
            QListWidget::item:selected {{
                background-color: {self.accent_color};
                color: white;
            }}
        """)
        
        from grizzyclaw.workspaces.workspace import WORKSPACE_TEMPLATES
        for key, template in WORKSPACE_TEMPLATES.items():
            item = QListWidgetItem(f"{template.icon} {template.name}\n   {template.description}")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.template_list.addItem(item)
        
        self.template_list.setCurrentRow(0)
        layout.addWidget(self.template_list, 1)
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.input_bg};
                color: {self.fg_color};
                border: 1px solid {self.border_color};
                border-radius: 6px;
                padding: 8px 20px;
            }}
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        create_btn = QPushButton("Create")
        create_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.accent_color};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 8px 20px;
            }}
        """)
        create_btn.clicked.connect(self.accept_template)
        btn_layout.addWidget(create_btn)
        
        layout.addLayout(btn_layout)
    
    def accept_template(self):
        """Accept the selected template"""
        current = self.template_list.currentItem()
        if current:
            self.selected_template = current.data(Qt.ItemDataRole.UserRole)
        self.accept()
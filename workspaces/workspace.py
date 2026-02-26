"""Workspace model for multi-agent workspaces"""

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class WorkspaceConfig:
    """Configuration for a workspace"""
    
    # LLM Settings
    llm_provider: str = "ollama"
    llm_model: str = "llama3.2"
    temperature: float = 0.7
    max_tokens: int = 131072
    
    # API Keys (optional override per workspace)
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    
    # Custom URLs
    ollama_url: str = "http://localhost:11434"
    lmstudio_url: str = "http://localhost:1234/v1"
    custom_provider_url: Optional[str] = None
    
    # Behavior
    system_prompt: str = "You are a helpful AI assistant."
    rules_file: Optional[str] = None
    use_agents_sdk: bool = False  # Use OpenAI Agents SDK + LiteLLM for improved coding (multi-provider)
    agents_sdk_max_turns: int = 25  # Max agent turns when use_agents_sdk (tool-call iterations)
    max_agentic_iterations: Optional[int] = None  # Override Settings max_agentic_iterations per workspace
    enabled_skills: List[str] = field(default_factory=list)
    
    # Memory
    memory_enabled: bool = True
    memory_file: Optional[str] = None  # Custom DB per workspace
    max_context_length: int = 4000
    max_session_messages: int = 20  # Context window: trim older turns, keep tool-heavy ones
    
    # Safety (guardrails per workspace)
    safety_policy: Optional[Dict[str, Any]] = None  # SafetyPolicy as dict
    safety_content_filter: bool = True
    safety_pii_redact_logs: bool = True
    enable_inter_agent: bool = False
    inter_agent_channel: Optional[str] = None  # Optional channel; only workspaces on same channel can message each other
    proactive_habits: bool = False
    proactive_screen: bool = False
    proactive_autonomy: bool = False  # Continuous background loop for predictive prep and tasks
    proactive_autonomy_interval_minutes: int = 15  # How often the autonomy loop runs (5, 15, 30)
    proactive_file_triggers: bool = False  # Trigger on file changes / Git events (see triggers.json)
    use_shared_memory: bool = False
    swarm_role: str = "none"
    swarm_auto_delegate: bool = False  # Leader: parse response for @mentions and run delegations
    swarm_consensus: bool = False      # Leader: after delegations, synthesize specialist replies into one

    # Sub-agents (agent-spawned child runs: parallel tasks, orchestrator pattern)
    subagents_enabled: bool = False
    subagents_max_depth: int = 2        # Main=0, depth 1 can spawn; depth 2 cannot (or set 3 for one more level)
    subagents_max_children: int = 5    # Max concurrent child runs per parent
    subagents_run_timeout_seconds: int = 0   # 0 = no timeout; default for spawned runs
    subagents_model: Optional[str] = None    # Optional model override for sub-agent runs

    # Channels (which channels this workspace responds to)
    telegram_enabled: bool = False
    telegram_bot_token: Optional[str] = None
    webchat_enabled: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkspaceConfig":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Workspace:
    """A workspace with isolated agent configuration"""
    
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = "Default"
    description: str = ""
    icon: str = "🤖"
    color: str = "#007AFF"
    order: int = field(default=0)
    avatar_path: Optional[str] = None  # Custom or VL-generated avatar image path/URL

    config: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    
    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    is_active: bool = True
    is_default: bool = False
    session_count: int = 0
    message_count: int = 0
    total_response_time_ms: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    feedback_up: int = 0
    feedback_down: int = 0

    # Runtime state (not persisted)
    agent: Any = field(default=None, repr=False)

    @property
    def avg_response_time_ms(self) -> float:
        return self.total_response_time_ms / self.message_count if self.message_count > 0 else 0.0

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def quality_score(self) -> float:
        total_feedback = self.feedback_up + self.feedback_down
        return (self.feedback_up / total_feedback * 100) if total_feedback > 0 else 0.0

    def __post_init__(self):
        if isinstance(self.config, dict):
            self.config = WorkspaceConfig.from_dict(self.config)
        if isinstance(self.created_at, str):
            self.created_at = datetime.fromisoformat(self.created_at)
        if isinstance(self.updated_at, str):
            self.updated_at = datetime.fromisoformat(self.updated_at)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for persistence"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "color": self.color,
            "order": self.order,
            "avatar_path": self.avatar_path,
            "config": self.config.to_dict(), 
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "is_active": self.is_active,
            "is_default": self.is_default,
            "session_count": self.session_count,
            "message_count": self.message_count,
            "total_response_time_ms": self.total_response_time_ms,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "feedback_up": self.feedback_up,
            "feedback_down": self.feedback_down,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Workspace":
        """Create from dictionary"""
        data = dict(data)
        data.setdefault("avatar_path", None)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
    
    def get_memory_db_path(self) -> str:
        """Get the memory database path for this workspace"""
        if self.config.use_shared_memory:
            return "shared_inter_agent.db"
        if self.config.memory_file:
            return self.config.memory_file
        # Default: separate DB per workspace
        return f"workspace_{self.id}.db"
    
    def update(self, **kwargs):
        """Update workspace attributes"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.updated_at = datetime.now()


# Preset workspace templates
WORKSPACE_TEMPLATES = {
    "default": Workspace(
        name="Default",
        description="General-purpose assistant",
        icon="🤖",
        color="#007AFF",
        config=WorkspaceConfig(
            max_tokens=131072,
            system_prompt="You are GrizzyClaw, a helpful AI assistant with memory. You can remember previous conversations and use that context to help the user.\n\n## SWARM LEADER\nBreak complex tasks into subtasks. Delegate by writing lines like:\n@research Research X.\n@coding Code Y.\n@personal Plan Z.\nUse workspace slugs: @research, @coding, @personal, @writing, @planning, or @code_assistant etc. Your delegations are executed automatically; specialist replies are then synthesized into one answer when consensus is on. Use shared memory to recall context.",
            enable_inter_agent=True,
            use_shared_memory=True,
            swarm_role="leader",
            swarm_auto_delegate=True,
            swarm_consensus=True,
            proactive_habits=True
        )
    ),
    "coding": Workspace(
        name="Code Assistant",
        description="Specialized for programming tasks",
        icon="💻",
        color="#34C759",
        config=WorkspaceConfig(
            system_prompt="You are a senior software engineer assistant. Help with coding, debugging, code review, and software architecture. Be precise and provide working code examples.\n\n## CREATING FILES / APPS\nWhen asked to build, create, or write an app or files: use TOOL_CALL with fast-filesystem. The tool is \"fast_write_file\" (path, content). First call fast_list_allowed_directories to see writable paths. The path the user gives is the TARGET FOLDER—write files directly into it (e.g. /Users/ewg/ZZZZ/TodoApp.swift). Use the existing folder or it will be created. Do NOT add a subfolder with the same name (e.g. not ZZZZ/ZZZZ/). Output TOOL_CALL for each file. NEVER just describe—actually create them. If you cannot output TOOL_CALL, output each file as: ### Filename.swift then ```swift\\n<full source>\\n```.\n\n## FAST-FILESYSTEM (exact names, copy exactly)\n- fast_list_allowed_directories (NOT fast_list_allowed_DIRECTORIES)\n- fast_write_file\n- fast_create_directory (NOT fast_make_dir, fast_make_directories, fast_createdirectory—use underscore between create and directory)\n- fast_list_directory, fast_get_directory_tree\n- recursive: use boolean true or false, NEVER the string \"true\" or \"false\"\n- Paths: on macOS use /Users/ (capital U), never /users/. Example: /Users/ewg/ToDo\n\nMatch the user's scope: if they ask for robust, feature-rich, feature-filled, professional, or beautiful—implement many features, a polished UI, preferences/settings, and do not default to minimal implementations. Honor adjectives like \"do not scrimp\" or \"plenty of features.\"\n\nWhen given a detailed plan, phased implementation, or step-by-step guide: implement the FULL plan. Create ALL files specified. Output MULTIPLE TOOL_CALLs in the same response—one per file. Do NOT stop after one file.\n\n## SPECIALIST_CODING\nFocus on coding tasks. If needed, @leader with summary.",
            temperature=0.55,
            max_tokens=131072,
            enable_inter_agent=True,
            use_shared_memory=True,
            swarm_role="specialist_coding",
            proactive_habits=False
        )
    ),
    "writing": Workspace(
        name="Writing Assistant",
        description="Creative writing and content creation",
        icon="✍️",
        color="#FF9500",
        config=WorkspaceConfig(
            max_tokens=131072,
            system_prompt="You are a professional writing assistant. Help with creative writing, editing, proofreading, and content creation. Be articulate and suggest improvements.\n\n## SPECIALIST_WRITING\nFocus on writing tasks. Respond concisely. If needed, @leader with summary.",
            temperature=0.8,
            enable_inter_agent=True,
            use_shared_memory=True,
            swarm_role="specialist_writing",
            proactive_habits=False
        )
    ),
    "research": Workspace(
        name="Research Assistant",
        description="Information gathering and analysis",
        icon="🔬",
        color="#5856D6",
        config=WorkspaceConfig(
            max_tokens=131072,
            system_prompt="You are a research assistant. Help gather information, summarize findings, analyze data, and provide well-sourced insights. Be thorough and cite sources when possible.\n\n## SPECIALIST_RESEARCH\nFocus on research tasks. Respond concisely. If needed, @leader with summary.",
            temperature=0.5,
            enabled_skills=["web_search"],
            enable_inter_agent=True,
            use_shared_memory=True,
            swarm_role="specialist_research",
            proactive_habits=False
        )
    ),
    "personal": Workspace(
        name="Personal Assistant",
        description="Daily tasks and reminders",
        icon="📋",
        color="#FF2D55",
        config=WorkspaceConfig(
            max_tokens=131072,
            system_prompt="You are a personal assistant. Help with scheduling, reminders, task management, and daily planning. Be organized and proactive.\n\n## SPECIALIST_PERSONAL\nFocus on personal tasks. Respond concisely. If needed, @leader with summary.",
            enabled_skills=["scheduler"],
            enable_inter_agent=True,
            use_shared_memory=True,
            swarm_role="specialist_personal",
            proactive_habits=True
        )
    ),
    "planning": Workspace(
        name="Planning Assistant",
        description="Project planning, roadmaps, and strategy",
        icon="🗺️",
        color="#00C7BE",
        config=WorkspaceConfig(
            max_tokens=131072,
            system_prompt="You are a planning assistant. Help with project planning, roadmaps, milestones, task breakdown, sprint planning, resource allocation, and decision frameworks. Structure ideas into clear phases, dependencies, and timelines. Be thorough and methodical.\n\n## SPECIALIST_PLANNING\nFocus on planning tasks. Break down complex goals into actionable steps. If needed, @leader with summary.",
            temperature=0.5,
            enable_inter_agent=True,
            use_shared_memory=True,
            swarm_role="specialist_planning",
            proactive_habits=False
        )
    ),
}

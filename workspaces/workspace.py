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
    max_tokens: int = 2000
    
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
    
    config: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    
    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    is_active: bool = True
    is_default: bool = False
    
    # Runtime state (not persisted)
    agent: Any = field(default=None, repr=False)
    session_count: int = 0
    message_count: int = 0
    
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
            "config": self.config.to_dict(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "is_active": self.is_active,
            "is_default": self.is_default,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Workspace":
        """Create from dictionary"""
        return cls(**data)
    
    def get_memory_db_path(self) -> str:
        """Get the memory database path for this workspace"""
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
            system_prompt="You are GrizzyClaw, a helpful AI assistant with memory. You can remember previous conversations and use that context to help the user."
        )
    ),
    "coding": Workspace(
        name="Code Assistant",
        description="Specialized for programming tasks",
        icon="💻",
        color="#34C759",
        config=WorkspaceConfig(
            system_prompt="You are a senior software engineer assistant. Help with coding, debugging, code review, and software architecture. Be precise and provide working code examples.",
            temperature=0.3,
            max_tokens=4000,
        )
    ),
    "writing": Workspace(
        name="Writing Assistant",
        description="Creative writing and content creation",
        icon="✍️",
        color="#FF9500",
        config=WorkspaceConfig(
            system_prompt="You are a professional writing assistant. Help with creative writing, editing, proofreading, and content creation. Be articulate and suggest improvements.",
            temperature=0.8,
        )
    ),
    "research": Workspace(
        name="Research Assistant",
        description="Information gathering and analysis",
        icon="🔬",
        color="#5856D6",
        config=WorkspaceConfig(
            system_prompt="You are a research assistant. Help gather information, summarize findings, analyze data, and provide well-sourced insights. Be thorough and cite sources when possible.",
            temperature=0.5,
            enabled_skills=["web_search"],
        )
    ),
    "personal": Workspace(
        name="Personal Assistant",
        description="Daily tasks and reminders",
        icon="📋",
        color="#FF2D55",
        config=WorkspaceConfig(
            system_prompt="You are a personal assistant. Help with scheduling, reminders, task management, and daily planning. Be organized and proactive.",
            enabled_skills=["scheduler"],
        )
    ),
}

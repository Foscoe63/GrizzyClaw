"""Configuration management for GrizzyClaw"""

import os
import sys
import yaml
from pathlib import Path
from typing import Optional, List, Dict
from pydantic import Field
from pydantic_settings import BaseSettings


# Deprecated Anthropic models (retired) -> replacement
ANTHROPIC_DEPRECATED = {
    "claude-3-5-sonnet-20241022": "claude-sonnet-4-5-20250929",
    "claude-3-5-sonnet-20240620": "claude-sonnet-4-5-20250929",
    "claude-3-5-haiku-20241022": "claude-haiku-4-5-20251001",
    "claude-3-7-sonnet-20250219": "claude-sonnet-4-5-20250929",
    "claude-3-opus-20240229": "claude-opus-4-6",
    "claude-3-sonnet-20240229": "claude-sonnet-4-5-20250929",
    "claude-3-haiku-20240307": "claude-haiku-4-5-20251001",
}


def get_config_path() -> Path:
    """Return the path to config.yaml used for both loading and saving.
    When running as a frozen app, use a user-writable path so saves persist.
    When running from source, use the project root."""
    if getattr(sys, "frozen", False):
        config_dir = Path.home() / ".grizzyclaw"
        return config_dir / "config.yaml"
    return Path(__file__).resolve().parent.parent / "config.yaml"


class Settings(BaseSettings):
    """Application settings with environment variable support"""

    # Application
    app_name: str = "GrizzyClaw"
    debug: bool = Field(default=False, alias="GRIZZYCLAW_DEBUG")
    secret_key: str = Field(
        default="change-me-in-production", alias="GRIZZYCLAW_SECRET_KEY"
    )

    # Database
    database_url: str = Field(default="sqlite:///grizzyclaw.db", alias="DATABASE_URL")

    # LLM Providers
    ollama_url: str = Field(default="http://localhost:11434", alias="OLLAMA_URL")
    ollama_model: str = Field(default="llama3.2", alias="OLLAMA_MODEL")

    lmstudio_url: str = Field(default="http://localhost:1234/v1", alias="LMSTUDIO_URL")
    lmstudio_model: str = Field(default="local-model", alias="LMSTUDIO_MODEL")

    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o", alias="OPENAI_MODEL")

    anthropic_api_key: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-4-5-20250929", alias="ANTHROPIC_MODEL")

    openrouter_api_key: Optional[str] = Field(default=None, alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field(default="openai/gpt-4o", alias="OPENROUTER_MODEL")

    custom_provider_url: Optional[str] = Field(default=None, alias="CUSTOM_PROVIDER_URL")
    custom_provider_api_key: Optional[str] = Field(default=None, alias="CUSTOM_PROVIDER_API_KEY")
    custom_provider_model: str = Field(default="", alias="CUSTOM_PROVIDER_MODEL")

    # Default LLM settings
    default_llm_provider: str = Field(default="ollama", alias="DEFAULT_LLM_PROVIDER")
    default_model: str = Field(default="llama3.2", alias="DEFAULT_MODEL")
    max_tokens: int = Field(default=2000, alias="MAX_TOKENS")  # Per-response limit; workspace overrides
    # Model routing: use a smaller/faster model for simple tasks (e.g. list files, short Q&A)
    simple_task_provider: Optional[str] = Field(default=None, alias="SIMPLE_TASK_PROVIDER")
    simple_task_model: Optional[str] = Field(default=None, alias="SIMPLE_TASK_MODEL")

    # Channels
    telegram_bot_token: Optional[str] = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_webhook_url: Optional[str] = Field(
        default=None, alias="TELEGRAM_WEBHOOK_URL"
    )
    whatsapp_session_path: Optional[str] = Field(default="~/.grizzyclaw/whatsapp_session", alias="WHATSAPP_SESSION_PATH")

    # Gmail Pub/Sub
    gmail_credentials_json: Optional[str] = Field(default=None, alias="GMAIL_CREDENTIALS_JSON")
    gmail_pubsub_topic: Optional[str] = Field(default=None, alias="GMAIL_PUBSUB_TOPIC")
    gmail_pubsub_audience: Optional[str] = Field(
        default=None, alias="GMAIL_PUBSUB_AUDIENCE"
    )  # Push endpoint URL for JWT verification (e.g. https://your-host/gmail)

    # Google APIs (Calendar, Gmail)
    google_client_id: Optional[str] = Field(default=None, alias="GOOGLE_CLIENT_ID")
    google_client_secret: Optional[str] = Field(default=None, alias="GOOGLE_CLIENT_SECRET")

    # GitHub
    github_token: Optional[str] = Field(default=None, alias="GITHUB_TOKEN")

    # Prompts & Rules
    system_prompt: str = Field(default="You are GrizzyClaw, a helpful AI assistant with memory. You can remember previous conversations and use that context to help the user.", alias="SYSTEM_PROMPT")
    rules_file: Optional[str] = Field(default=None, alias="RULES_FILE")

    # Skills & MCP
    hf_token: Optional[str] = Field(default=None, alias="HF_TOKEN")
    enabled_skills: list[str] = Field(default_factory=list, alias="ENABLED_SKILLS")
    mcp_servers_file: str = Field(default="~/.grizzyclaw/grizzyclaw.json", alias="MCP_SERVERS_FILE")
    mcp_marketplace_url: Optional[str] = Field(default=None, alias="MCP_MARKETPLACE_URL")

    # Security
    jwt_secret: str = Field(default="your-jwt-secret", alias="JWT_SECRET")
    gateway_auth_token: Optional[str] = Field(
        default=None, alias="GATEWAY_AUTH_TOKEN"
    )
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24

    # Agent queue
    queue_enabled: bool = Field(default=False, alias="QUEUE_ENABLED")
    queue_max_per_session: int = Field(default=50, alias="QUEUE_MAX_PER_SESSION")

    # Gateway rate limiting (per client)
    gateway_rate_limit_requests: int = Field(
        default=60, alias="GATEWAY_RATE_LIMIT_REQUESTS"
    )
    gateway_rate_limit_window: int = Field(
        default=60, alias="GATEWAY_RATE_LIMIT_WINDOW"
    )

    # Rate limiting
    rate_limit_requests: int = 100
    rate_limit_window: int = 60

    # Media / Transcription
    transcription_provider: str = Field(
        default="openai", alias="TRANSCRIPTION_PROVIDER"
    )  # "local" or "openai"
    input_device_index: Optional[int] = Field(
        default=None, alias="INPUT_DEVICE_INDEX"
    )  # Deprecated: use input_device_name
    input_device_name: Optional[str] = Field(
        default=None, alias="INPUT_DEVICE_NAME"
    )  # Device name substring for sounddevice; more reliable than index in bundled app
    media_retention_days: int = Field(
        default=7, alias="MEDIA_RETENTION_DAYS"
    )
    media_max_size_mb: int = Field(
        default=0, alias="MEDIA_MAX_SIZE_MB"
    )  # 0 = no limit; delete oldest when over

    # Memory
    max_context_length: int = 4000
    max_session_messages: int = 20
    memory_retrieval_limit: int = 10

    # Agent autonomy & strength
    max_agentic_iterations: int = Field(default=10, alias="MAX_AGENTIC_ITERATIONS")  # Tool-use rounds per turn
    agent_reflection_enabled: bool = Field(default=True, alias="AGENT_REFLECTION_ENABLED")  # Prompt to continue or answer after tool results
    agent_plan_before_tools: bool = Field(default=False, alias="AGENT_PLAN_BEFORE_TOOLS")  # Ask for PLAN = [...] on complex tasks
    agent_tool_result_max_chars: int = Field(default=4000, alias="AGENT_TOOL_RESULT_MAX_CHARS")  # Truncate/summarize larger tool results
    agent_retry_on_tool_failure: bool = Field(default=True, alias="AGENT_RETRY_ON_TOOL_FAILURE")  # One retry with feedback on tool error
    session_persistence: bool = Field(
        default=True, alias="SESSION_PERSISTENCE"
    )  # Persist chat sessions to disk across restarts

    # Safety
    safety_content_filter: bool = True
    safety_pii_redact_logs: bool = True
    safety_policy: Optional[Dict] = None  # Per-workspace policy dict
    exec_commands_enabled: bool = Field(
        default=False, alias="EXEC_COMMANDS_ENABLED"
    )  # Allow agent to run shell commands (requires approval in GUI)
    exec_safe_commands_skip_approval: bool = Field(
        default=True, alias="EXEC_SAFE_COMMANDS_SKIP_APPROVAL"
    )  # Skip approval for ls, df, pwd, etc.
    exec_safe_commands: List[str] = Field(
        default_factory=lambda: ["ls", "df", "pwd", "whoami", "date", "uptime", "echo", "which", "type"],
        alias="EXEC_SAFE_COMMANDS",
    )  # Commands that can skip approval when above is True
    exec_sandbox_enabled: bool = Field(
        default=False, alias="EXEC_SANDBOX_ENABLED"
    )  # Run approved commands in restricted env (limited PATH, no network when possible)
    pre_send_health_check: bool = Field(
        default=False, alias="PRE_SEND_HEALTH_CHECK"
    )  # Ping LLM provider before sending; warn if unreachable

    # Logging & Observability
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_json: bool = Field(default=False, alias="LOG_JSON")
    log_pii_redact: bool = Field(default=True, alias="LOG_PII_REDACT")
    tracing_enabled: bool = Field(default=False, alias="TRACING_ENABLED")

    # Voice (ElevenLabs for high-quality TTS)
    elevenlabs_api_key: Optional[str] = Field(default=None, alias="ELEVENLABS_API_KEY")
    elevenlabs_voice_id: str = Field(
        default="21m00Tcm4TlvDq8ikWAM", alias="ELEVENLABS_VOICE_ID"
    )
    tts_provider: str = Field(default="auto", alias="TTS_PROVIDER")  # auto, elevenlabs, pyttsx3, say

    # Appearance
    theme: str = "Light"
    font_family: str = "System Default"
    font_size: int = 13
    compact_mode: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        populate_by_name = True

    @classmethod
    def from_file(cls, path: str) -> "Settings":
        """Load settings from YAML file"""
        with open(path, "r") as f:
            config = yaml.safe_load(f)
        if config and "anthropic_model" in config:
            old = config["anthropic_model"]
            if old in ANTHROPIC_DEPRECATED:
                config["anthropic_model"] = ANTHROPIC_DEPRECATED[old]
        return cls(**config)

    def to_file(self, path: str):
        """Save settings to YAML file. Creates parent directory if needed."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False)

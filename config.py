"""Configuration management for GrizzyClaw"""

import os
import sys
import yaml
from pathlib import Path
from typing import Optional, List, Dict
from pydantic import Field
from pydantic_settings import BaseSettings


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
    anthropic_model: str = Field(default="claude-3-5-sonnet-20241022", alias="ANTHROPIC_MODEL")

    openrouter_api_key: Optional[str] = Field(default=None, alias="OPENROUTER_API_KEY")
    openrouter_model: str = Field(default="openai/gpt-4o", alias="OPENROUTER_MODEL")

    custom_provider_url: Optional[str] = Field(default=None, alias="CUSTOM_PROVIDER_URL")
    custom_provider_api_key: Optional[str] = Field(default=None, alias="CUSTOM_PROVIDER_API_KEY")
    custom_provider_model: str = Field(default="", alias="CUSTOM_PROVIDER_MODEL")

    # Default LLM settings
    default_llm_provider: str = Field(default="ollama", alias="DEFAULT_LLM_PROVIDER")
    default_model: str = Field(default="llama3.2", alias="DEFAULT_MODEL")

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

    # Prompts & Rules
    system_prompt: str = Field(default="You are GrizzyClaw, a helpful AI assistant with memory. You can remember previous conversations and use that context to help the user.", alias="SYSTEM_PROMPT")
    rules_file: Optional[str] = Field(default=None, alias="RULES_FILE")

    # Skills & MCP
    hf_token: Optional[str] = Field(default=None, alias="HF_TOKEN")
    enabled_skills: list[str] = Field(default_factory=list, alias="ENABLED_SKILLS")
    mcp_servers_file: str = Field(default="~/.grizzyclaw/grizzyclaw.json", alias="MCP_SERVERS_FILE")

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

    # Safety
    safety_content_filter: bool = True
    safety_pii_redact_logs: bool = True
    safety_policy: Optional[Dict] = None  # Per-workspace policy dict

    # Logging & Observability
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_json: bool = Field(default=False, alias="LOG_JSON")
    log_pii_redact: bool = Field(default=True, alias="LOG_PII_REDACT")
    tracing_enabled: bool = Field(default=False, alias="TRACING_ENABLED")

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
        return cls(**config)

    def to_file(self, path: str):
        """Save settings to YAML file. Creates parent directory if needed."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False)

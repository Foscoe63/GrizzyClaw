"""Configurable safety policies per workspace."""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class SafetyLevel(str, Enum):
    """Safety strictness level."""

    OFF = "off"  # No filtering
    LOW = "low"  # Minimal filtering
    MEDIUM = "medium"  # Default blocklist
    HIGH = "high"  # Stricter, more patterns


@dataclass
class SafetyPolicy:
    """Per-workspace safety configuration."""

    level: SafetyLevel = SafetyLevel.MEDIUM
    content_filter_enabled: bool = True
    pii_redact_logs: bool = True
    custom_blocklist: List[str] = field(default_factory=list)
    custom_allowlist: Optional[List[str]] = None  # Override blocklist for specific terms

    def to_dict(self) -> dict:
        return {
            "level": self.level.value,
            "content_filter_enabled": self.content_filter_enabled,
            "pii_redact_logs": self.pii_redact_logs,
            "custom_blocklist": self.custom_blocklist,
            "custom_allowlist": self.custom_allowlist,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SafetyPolicy":
        level = SafetyLevel(data.get("level", "medium"))
        return cls(
            level=level,
            content_filter_enabled=data.get("content_filter_enabled", True),
            pii_redact_logs=data.get("pii_redact_logs", True),
            custom_blocklist=data.get("custom_blocklist", []),
            custom_allowlist=data.get("custom_allowlist"),
        )

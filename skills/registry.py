"""Skills registry - discoverable skills ecosystem"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import json
import logging

logger = logging.getLogger(__name__)


@dataclass
class SkillMetadata:
    """Metadata for a skill in the ecosystem."""
    id: str
    name: str
    description: str
    icon: str = "⚡"
    config_schema: Optional[Dict[str, Any]] = None
    source: str = "builtin"  # builtin, local, hf


# Built-in skills registry
SKILL_REGISTRY: Dict[str, SkillMetadata] = {
    "web_search": SkillMetadata(
        id="web_search",
        name="Web Search",
        description="Search the web for real-time information via DuckDuckGo",
        icon="🔍",
        source="builtin",
    ),
    "filesystem": SkillMetadata(
        id="filesystem",
        name="File System",
        description="Read, write, and manage files on your system",
        icon="📁",
        source="builtin",
    ),
    "documentation": SkillMetadata(
        id="documentation",
        name="Documentation",
        description="Query library documentation via Context7",
        icon="📚",
        source="builtin",
    ),
    "browser": SkillMetadata(
        id="browser",
        name="Browser Automation",
        description="Navigate, screenshot, and interact with web pages",
        icon="🌐",
        source="builtin",
    ),
    "memory": SkillMetadata(
        id="memory",
        name="Memory",
        description="Remember and recall information across conversations",
        icon="🧠",
        source="builtin",
    ),
    "scheduler": SkillMetadata(
        id="scheduler",
        name="Scheduler",
        description="Schedule tasks and reminders",
        icon="⏰",
        source="builtin",
    ),
    "calendar": SkillMetadata(
        id="calendar",
        name="Google Calendar",
        description="List, create, update calendar events",
        icon="📅",
        source="builtin",
    ),
    "gmail": SkillMetadata(
        id="gmail",
        name="Gmail",
        description="Send emails, reply to threads",
        icon="📧",
        source="builtin",
    ),
    "github": SkillMetadata(
        id="github",
        name="GitHub",
        description="Manage PRs, issues, repos",
        icon="💻",
        source="builtin",
    ),
    "mcp_marketplace": SkillMetadata(
        id="mcp_marketplace",
        name="MCP Marketplace",
        description="Discover and install ClawHub MCP servers",
        icon="🛒",
        source="builtin",
    ),
}


def get_available_skills() -> List[SkillMetadata]:
    """Return all skills in the registry."""
    return list(SKILL_REGISTRY.values())


def get_skill(id: str) -> Optional[SkillMetadata]:
    """Get skill metadata by id."""
    return SKILL_REGISTRY.get(id)


def load_user_skills(data_dir: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Load user's skill configs from skills.json."""
    data_dir = data_dir or Path.home() / ".grizzyclaw"
    skills_file = data_dir / "skills.json"
    if not skills_file.exists():
        return {}
    try:
        with open(skills_file, "r") as f:
            data = json.load(f)
        return data.get("skills", {})
    except Exception as e:
        logger.warning(f"Could not load skills.json: {e}")
        return {}


def save_user_skills(skills: Dict[str, Dict[str, Any]], data_dir: Optional[Path] = None) -> bool:
    """Save user's skill configs."""
    data_dir = data_dir or Path.home() / ".grizzyclaw"
    data_dir.mkdir(parents=True, exist_ok=True)
    skills_file = data_dir / "skills.json"
    try:
        with open(skills_file, "w") as f:
            json.dump({"skills": skills}, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Could not save skills.json: {e}")
        return False

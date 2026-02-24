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
    executor: Optional[Any] = None  # Callable for dynamic execution


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

DYNAMIC_SKILL_REGISTRY: Dict[str, SkillMetadata] = {}

def load_dynamic_skills(data_dir: Optional[Path] = None):
    """Dynamically load Python skills from ~/.grizzyclaw/plugins/skills/"""
    import importlib.util
    import sys
    
    data_dir = data_dir or Path.home() / ".grizzyclaw"
    plugins_dir = data_dir / "plugins" / "skills"
    
    # Create the directory if it doesn't exist
    plugins_dir.mkdir(parents=True, exist_ok=True)
    
    for py_file in plugins_dir.glob("*.py"):
        if py_file.name == "__init__.py":
            continue
        try:
            module_name = f"grizzyclaw_plugin_{py_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, str(py_file))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                
                # Expect the module to define a SKILL_METADATA and optionally execute_skill
                if hasattr(module, "SKILL_METADATA"):
                    meta = module.SKILL_METADATA
                    if isinstance(meta, dict):
                        skill_id = meta.get("id", py_file.stem)
                        DYNAMIC_SKILL_REGISTRY[skill_id] = SkillMetadata(
                            id=skill_id,
                            name=meta.get("name", skill_id),
                            description=meta.get("description", ""),
                            icon=meta.get("icon", "🔌"),
                            config_schema=meta.get("config_schema"),
                            source="plugin",
                            executor=getattr(module, "execute", None)
                        )
                        logger.info(f"Loaded dynamic skill plugin: {skill_id}")
        except Exception as e:
            logger.error(f"Failed to load dynamic skill {py_file}: {e}")

# Call it once on import
load_dynamic_skills()

def get_available_skills() -> List[SkillMetadata]:
    """Return all skills in the registry."""
    return list(SKILL_REGISTRY.values()) + list(DYNAMIC_SKILL_REGISTRY.values())


def get_skill(id: str) -> Optional[SkillMetadata]:
    """Get skill metadata by id."""
    return SKILL_REGISTRY.get(id) or DYNAMIC_SKILL_REGISTRY.get(id)


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

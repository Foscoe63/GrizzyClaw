"""Workspace manager for handling multiple agent workspaces"""

import json
import logging
from pathlib import Path
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any, TYPE_CHECKING

from grizzyclaw.config import Settings
from grizzyclaw.memory.sqlite_store import SQLiteMemoryStore
from .workspace import Workspace, WorkspaceConfig, WORKSPACE_TEMPLATES
from .swarm_events import SwarmEventBus

if TYPE_CHECKING:
    from grizzyclaw.agent.core import AgentCore

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """Manage multiple agent workspaces with isolated configurations"""
    
    def __init__(self, data_dir: Optional[str] = None):
        """Initialize workspace manager
        
        Args:
            data_dir: Directory for workspace data (default: ~/.grizzyclaw)
        """
        self.data_dir = Path(data_dir or Path.home() / ".grizzyclaw")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.workspaces_file = self.data_dir / "workspaces.json"
        self._user_templates_file = self.data_dir / "workspace_templates.json"
        self.workspaces: Dict[str, Workspace] = {}
        self.active_workspace_id: Optional[str] = None
        self.swarm_event_bus = SwarmEventBus()
        from grizzyclaw.agent.subagent_registry import SubagentRegistry
        self.subagent_registry = SubagentRegistry()

        # Load existing workspaces or create default
        self._load_workspaces()
    
    def _load_workspaces(self):
        """Load workspaces from disk"""
        if self.workspaces_file.exists():
            try:
                with open(self.workspaces_file, "r") as f:
                    data = json.load(f)
                
                for ws_data in data.get("workspaces", []):
                    workspace = Workspace.from_dict(ws_data)
                    self.workspaces[workspace.id] = workspace
                
                self.active_workspace_id = data.get("active_workspace_id")
                if not self.workspaces:
                    self._create_default_workspace()
                else:
                    if not self.active_workspace_id or self.active_workspace_id not in self.workspaces:
                        self.active_workspace_id = next(iter(self.workspaces.keys()))
                logger.info(f"Loaded {len(self.workspaces)} workspaces")
                
            except Exception as e:
                logger.error(f"Failed to load workspaces: {e}")
                self._create_default_workspace()
        else:
            self._create_default_workspace()
    
    def _create_default_workspace(self):
        """Create a default workspace if none exists"""
        default = WORKSPACE_TEMPLATES["default"]
        default.id = "default"
        default.is_default = True
        self.workspaces["default"] = default
        self.active_workspace_id = "default"
        self._save_workspaces()
        logger.info("Created default workspace")
    
    def _save_workspaces(self):
        """Save workspaces to disk"""
        try:
            data = {
                "active_workspace_id": self.active_workspace_id,
                "workspaces": [ws.to_dict() for ws in self.workspaces.values()]
            }
            with open(self.workspaces_file, "w") as f:
                json.dump(data, f, indent=2)
            logger.debug("Saved workspaces")
        except Exception as e:
            logger.error(f"Failed to save workspaces: {e}")
    
    def get_all_templates(self) -> Dict[str, Workspace]:
        """Return built-in templates merged with user-defined templates from workspace_templates.json."""
        out = dict(WORKSPACE_TEMPLATES)
        if self._user_templates_file.exists():
            try:
                with open(self._user_templates_file, "r") as f:
                    data = json.load(f)
                for key, t in data.items():
                    if isinstance(t, dict) and "name" in t and "config" in t:
                        out[key] = Workspace(
                            name=t["name"],
                            description=t.get("description", ""),
                            icon=t.get("icon", "🤖"),
                            color=t.get("color", "#007AFF"),
                            config=WorkspaceConfig.from_dict(t["config"]),
                        )
            except Exception as e:
                logger.warning("Failed to load user workspace templates: %s", e)
        return out

    def add_user_template(self, key: str, workspace: Workspace) -> None:
        """Save a workspace as a user-defined template. Key must be a valid identifier (e.g. designer, my_template)."""
        key = key.strip().lower().replace(" ", "_")
        if not key:
            raise ValueError("Template key cannot be empty")
        data = {}
        if self._user_templates_file.exists():
            try:
                with open(self._user_templates_file, "r") as f:
                    data = json.load(f)
            except Exception:
                pass
        data[key] = {
            "name": workspace.name,
            "description": workspace.description or "",
            "icon": workspace.icon or "🤖",
            "color": workspace.color or "#007AFF",
            "config": workspace.config.to_dict(),
        }
        with open(self._user_templates_file, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("Saved user template: %s", key)

    def create_workspace(
        self,
        name: str,
        description: str = "",
        icon: str = "🤖",
        color: str = "#007AFF",
        template: Optional[str] = None,
        config: Optional[WorkspaceConfig] = None
    ) -> Workspace:
        """Create a new workspace
        
        Args:
            name: Workspace name
            description: Description
            icon: Emoji icon
            color: Color hex code
            template: Template name (default, coding, writing, research, personal, planning, designer, or user-defined)
            config: Custom configuration (overrides template)
        
        Returns:
            Created workspace
        """
        all_templates = self.get_all_templates()
        # Start from template if specified
        if template and template in all_templates:
            t = all_templates[template]
            workspace = Workspace(
                name=name,
                description=description or t.description,
                icon=icon or t.icon,
                color=color or t.color,
                order=len(self.get_workspaces_sorted()),
                config=WorkspaceConfig.from_dict(t.config.to_dict())
            )
        else:
            workspace = Workspace(
                name=name,
                description=description,
                icon=icon,
                color=color,
                order=len(self.get_workspaces_sorted()),
                config=config or WorkspaceConfig()
            )
        
        self.workspaces[workspace.id] = workspace
        self._save_workspaces()
        logger.info(f"Created workspace: {workspace.name} ({workspace.id})")
        return workspace
    
    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        """Get a workspace by ID"""
        return self.workspaces.get(workspace_id)
    
    def get_active_workspace(self) -> Optional[Workspace]:
        """Get the currently active workspace"""
        if self.active_workspace_id:
            return self.workspaces.get(self.active_workspace_id)
        return None
    
    def set_active_workspace(self, workspace_id: str) -> bool:
        """Set the active workspace
        
        Args:
            workspace_id: Workspace ID to activate
        
        Returns:
            True if successful
        """
        if workspace_id in self.workspaces:
            self.active_workspace_id = workspace_id
            self._save_workspaces()
            logger.info(f"Switched to workspace: {self.workspaces[workspace_id].name}")
            return True
        return False
    
    def update_workspace(self, workspace_id: str, **kwargs) -> Optional[Workspace]:
        """Update a workspace
        
        Args:
            workspace_id: Workspace ID
            **kwargs: Attributes to update
        
        Returns:
            Updated workspace or None
        """
        workspace = self.workspaces.get(workspace_id)
        if workspace:
            workspace.update(**kwargs)
            self._save_workspaces()
            return workspace
        return None
    
    def update_workspace_config(
        self,
        workspace_id: str,
        config_updates: Dict[str, Any],
    ) -> Optional[Workspace]:
        """Update workspace configuration.

        Merges config_updates into the full config and replaces workspace.config
        so all keys (including newly added ones like subagents_*) persist correctly.
        """
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        # Merge current config (as dict) with updates so no key is lost
        full = workspace.config.to_dict()
        for key, value in config_updates.items():
            if hasattr(workspace.config, key):
                full[key] = value
        workspace.config = WorkspaceConfig.from_dict(full)
        workspace.updated_at = datetime.now()
        self._save_workspaces()
        return workspace
    
    def delete_workspace(self, workspace_id: str) -> bool:
        """Delete a workspace
        
        Args:
            workspace_id: Workspace ID
        
        Returns:
            True if deleted
        """
        workspace = self.workspaces.get(workspace_id)
        if workspace and not workspace.is_default:
            del self.workspaces[workspace_id]
            
            # Switch to default if active was deleted
            if self.active_workspace_id == workspace_id:
                self.active_workspace_id = "default"
            
            self._save_workspaces()
            logger.info(f"Deleted workspace: {workspace.name}")
            return True
        return False
    
    def get_workspaces_sorted(self) -> List[Workspace]:
        """Get workspaces sorted by order"""
        return sorted(self.workspaces.values(), key=lambda ws: ws.order)

    def reorder_workspaces(self, ordered_ids: List[str]) -> None:
        """Reorder workspaces by the given list of workspace IDs (order = index)."""
        for i, wid in enumerate(ordered_ids):
            ws = self.workspaces.get(wid)
            if ws is not None:
                ws.order = i
        self._save_workspaces()
        logger.debug("Reordered workspaces: %s", ordered_ids)

    def list_workspaces(self) -> List[Workspace]:
        """List all workspaces. Ensures at least one (default) exists."""
        if not self.workspaces:
            self._create_default_workspace()
        return self.get_workspaces_sorted()
    
    def record_feedback(self, workspace_id: str, up: bool) -> None:
        """Record thumbs up (True) or thumbs down (False) for a workspace. Persists immediately."""
        ws = self.workspaces.get(workspace_id)
        if ws:
            if up:
                ws.feedback_up += 1
            else:
                ws.feedback_down += 1
            self._save_workspaces()

    def get_workspace_stats(self) -> Dict[str, Any]:
        """Get statistics about workspaces"""
        return {
            "total": len(self.get_workspaces_sorted()),
            "active_id": self.active_workspace_id,
            "workspaces": [
                {
                    "id": ws.id,
                    "name": ws.name,
                    "icon": ws.icon,
                    "is_active": ws.id == self.active_workspace_id,
                    "is_default": ws.is_default,
                    "session_count": ws.session_count,
                    "message_count": ws.message_count,
                    "avg_response_time_ms": round(ws.avg_response_time_ms, 1),
                    "total_tokens": ws.total_tokens,
                    "quality_score": round(ws.quality_score, 1),
                    "feedback_up": ws.feedback_up,
                    "feedback_down": ws.feedback_down,
                }
                for ws in self.get_workspaces_sorted()
            ]
        }
    
    def create_agent_for_workspace(
        self, 
        workspace_id: str,
        base_settings: Optional[Settings] = None
    ) -> "Optional[AgentCore]":
        """Create an agent configured for a specific workspace
        
        Args:
            workspace_id: Workspace ID
            base_settings: Base settings to override
        
        Returns:
            Configured AgentCore or None
        """
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        
        # Use a copy so we never mutate the main window's loaded settings (keeps
        # saved default provider and other prefs persistent across restarts).
        base = base_settings or Settings()
        settings = base.model_copy(deep=True)
        config = workspace.config
        
        # Override settings from workspace config (on the copy only).
        # Provider URLs (ollama_url, lmstudio_url) always come from base_settings (main Settings)
        # so "where to connect" is set in Settings → LLM Providers; workspace only picks provider + model.
        settings.default_llm_provider = config.llm_provider
        # Set provider-specific model so router uses it (no default_model override needed)
        if config.llm_provider == "ollama":
            settings.ollama_model = config.llm_model
        elif config.llm_provider == "lmstudio":
            settings.lmstudio_model = config.llm_model
        elif config.llm_provider == "openai":
            settings.openai_model = config.llm_model
        elif config.llm_provider == "anthropic":
            settings.anthropic_model = config.llm_model
        elif config.llm_provider == "openrouter":
            settings.openrouter_model = config.llm_model
        settings.system_prompt = config.system_prompt
        settings.max_context_length = config.max_context_length
        settings.max_tokens = getattr(config, "max_tokens", 2000)
        settings.max_session_messages = config.max_session_messages
        settings.safety_content_filter = getattr(config, "safety_content_filter", True)
        settings.safety_pii_redact_logs = getattr(config, "safety_pii_redact_logs", True)
        settings.safety_policy = getattr(config, "safety_policy", None)
        if config.custom_provider_url:
            settings.custom_provider_url = config.custom_provider_url
        
        # Override API keys if specified
        if config.openai_api_key:
            settings.openai_api_key = config.openai_api_key
        if config.anthropic_api_key:
            settings.anthropic_api_key = config.anthropic_api_key
        if config.openrouter_api_key:
            settings.openrouter_api_key = config.openrouter_api_key
        
        # Skills
        settings.enabled_skills = config.enabled_skills
        
        # Rules
        if config.rules_file:
            settings.rules_file = config.rules_file
        
        # Custom database path for workspace isolation.
        # Shared memory: one absolute DB per channel so all workspaces on that channel share it.
        if config.use_shared_memory:
            channel = config.inter_agent_channel or "default"
            db_path = self.data_dir / f"shared_memory_{channel}.db"
        else:
            db_path = self.data_dir / workspace.get_memory_db_path()
        settings.database_url = f"sqlite:///{db_path.resolve()}"

        # Lazy import to avoid circular import: agent.core -> workspaces.workspace -> manager -> agent.core
        from grizzyclaw.agent.core import AgentCore
        agent = AgentCore(settings)
        agent.workspace_manager = self
        agent.workspace_id = workspace_id
        agent.workspace_config = config
        agent.swarm_event_bus = self.swarm_event_bus
        agent.subagent_registry = self.subagent_registry
        workspace.agent = agent
        agent._ensure_swarm_subscriptions()

        logger.info(f"Created agent for workspace: {workspace.name}")
        return agent
    
    def get_or_create_agent(
        self, 
        workspace_id: str,
        base_settings: Optional[Settings] = None
    ) -> "Optional[AgentCore]":
        """Get existing agent or create new one for workspace
        
        Args:
            workspace_id: Workspace ID
            base_settings: Base settings for new agent
        
        Returns:
            AgentCore or None
        """
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        
        if workspace.agent:
            return workspace.agent
        
        return self.create_agent_for_workspace(workspace_id, base_settings)
    
    def duplicate_workspace(self, workspace_id: str, new_name: str) -> Optional[Workspace]:
        """Duplicate an existing workspace
        
        Args:
            workspace_id: Source workspace ID
            new_name: Name for the new workspace
        
        Returns:
            New workspace or None
        """
        source = self.workspaces.get(workspace_id)
        if not source:
            return None
        
        new_workspace = Workspace(
            name=new_name,
            description=f"Copy of {source.name}",
            icon=source.icon,
            color=source.color,
            order=len(self.get_workspaces_sorted()),
            config=WorkspaceConfig.from_dict(source.config.to_dict())
        )
        
        self.workspaces[new_workspace.id] = new_workspace
        self._save_workspaces()
        logger.info(f"Duplicated workspace {source.name} -> {new_name}")
        return new_workspace
    
    def export_workspace(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        """Export workspace configuration
        
        Args:
            workspace_id: Workspace ID
        
        Returns:
            Workspace data dict or None
        """
        workspace = self.workspaces.get(workspace_id)
        if workspace:
            return workspace.to_dict()
        return None
    
    def import_workspace(self, data: Dict[str, Any]) -> Optional[Workspace]:
        """Import a workspace from exported data
        
        Args:
            data: Workspace data dict
        
        Returns:
            Imported workspace or None
        """
        try:
            # Generate new ID to avoid conflicts
            data["id"] = Workspace().id
            data["is_default"] = False
            
            workspace = Workspace.from_dict(data)
            self.workspaces[workspace.id] = workspace
            self._save_workspaces()
            logger.info(f"Imported workspace: {workspace.name}")
            return workspace
        except Exception as e:
            logger.error(f"Failed to import workspace: {e}")
            return None


    def get_workspace_by_name(self, name: str) -> Optional[Workspace]:
        """Get workspace by name (case-insensitive)"""
        for ws in self.workspaces.values():
            if ws.name.lower() == name.lower():
                return ws
        return None

    def get_workspace_by_slug(self, slug: str) -> Optional[Workspace]:
        """Get workspace by slug (name lowercased, spaces → underscores). E.g. 'code_assistant' → 'Code Assistant'."""
        slug = slug.lower().strip()
        for ws in self.workspaces.values():
            ws_slug = ws.name.lower().replace(" ", "_").replace("-", "_")
            if ws_slug == slug:
                return ws
        return None

    def get_workspace_slug(self, workspace: Workspace) -> str:
        """Return @mention slug for a workspace (name lowercased, spaces → underscores)."""
        return workspace.name.lower().replace(" ", "_").replace("-", "_")

    def get_discoverable_specialist_slugs(
        self,
        inter_agent_channel: Optional[str] = None,
        exclude_workspace_id: Optional[str] = None,
    ) -> List[str]:
        """Return list of @mention slugs for workspaces that can receive delegations on the given channel."""
        slugs: List[str] = []
        for ws in self.workspaces.values():
            if not ws.config.enable_inter_agent or ws.id == exclude_workspace_id:
                continue
            if inter_agent_channel and ws.config.inter_agent_channel and ws.config.inter_agent_channel != inter_agent_channel:
                continue
            slugs.append(self.get_workspace_slug(ws))
        return sorted(slugs)

    async def send_message_to_workspace(
        self,
        from_id: str,
        to_id_or_name: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Send message from one workspace/agent to another"""
        target = (
            self.get_workspace(to_id_or_name)
            or self.get_workspace_by_name(to_id_or_name)
            or self.get_workspace_by_slug(to_id_or_name)
        )
        if not target:
            return "Target workspace not found."
        if not target.config.enable_inter_agent:
            return f"Target workspace '{target.name}' has inter-agent chat disabled."
        # If both sides use a channel, they must match
        from_ws = self.get_workspace(from_id)
        if from_ws and from_ws.config.inter_agent_channel and target.config.inter_agent_channel:
            if from_ws.config.inter_agent_channel != target.config.inter_agent_channel:
                return f"Target workspace is on channel '{target.config.inter_agent_channel}'; cannot message from '{from_ws.config.inter_agent_channel}'."
        try:
            delegation_context = context or {}
            if from_ws:
                delegation_context = {
                    **delegation_context,
                    "from_workspace_id": from_id,
                    "from_workspace_name": from_ws.name,
                    "task_summary": (message.strip().split("\n")[0][:120] if message else ""),
                }
            agent = self.get_or_create_agent(target.id)
            response = agent.process_message(
                f"inter-agent-{from_id}", message, context=delegation_context
            )
            if hasattr(response, "__aiter__"):
                chunks = []
                async for chunk in response:
                    chunks.append(chunk)
                response = "".join(chunks)
            elif hasattr(response, "__iter__"):
                response = "".join(response)
            return str(response)[:8000]  # Truncate very long responses; 8k for multi-file tool results
        except Exception as e:
            logger.error(f"Inter-agent error: {e}")
            return f"Error: {str(e)}"

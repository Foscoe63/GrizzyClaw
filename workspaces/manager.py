"""Workspace manager for handling multiple agent workspaces"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

from grizzyclaw.config import Settings
from grizzyclaw.agent.core import AgentCore
from grizzyclaw.memory.sqlite_store import SQLiteMemoryStore
from .workspace import Workspace, WorkspaceConfig, WORKSPACE_TEMPLATES

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
        self.workspaces: Dict[str, Workspace] = {}
        self.active_workspace_id: Optional[str] = None
        
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
            template: Template name (default, coding, writing, research, personal)
            config: Custom configuration (overrides template)
        
        Returns:
            Created workspace
        """
        # Start from template if specified
        if template and template in WORKSPACE_TEMPLATES:
            workspace = Workspace(
                name=name,
                description=description or WORKSPACE_TEMPLATES[template].description,
                icon=icon or WORKSPACE_TEMPLATES[template].icon,
                color=color or WORKSPACE_TEMPLATES[template].color,
                config=WorkspaceConfig.from_dict(WORKSPACE_TEMPLATES[template].config.to_dict())
            )
        else:
            workspace = Workspace(
                name=name,
                description=description,
                icon=icon,
                color=color,
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
        config_updates: Dict[str, Any]
    ) -> Optional[Workspace]:
        """Update workspace configuration
        
        Args:
            workspace_id: Workspace ID
            config_updates: Config attributes to update
        
        Returns:
            Updated workspace or None
        """
        workspace = self.workspaces.get(workspace_id)
        if workspace:
            for key, value in config_updates.items():
                if hasattr(workspace.config, key):
                    setattr(workspace.config, key, value)
            workspace.updated_at = workspace.updated_at.__class__.now()
            self._save_workspaces()
            return workspace
        return None
    
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
    
    def list_workspaces(self) -> List[Workspace]:
        """List all workspaces"""
        return list(self.workspaces.values())
    
    def get_workspace_stats(self) -> Dict[str, Any]:
        """Get statistics about workspaces"""
        return {
            "total": len(self.workspaces),
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
                }
                for ws in self.workspaces.values()
            ]
        }
    
    def create_agent_for_workspace(
        self, 
        workspace_id: str,
        base_settings: Optional[Settings] = None
    ) -> Optional[AgentCore]:
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
        
        # Custom database path for workspace isolation
        settings.database_url = f"sqlite:///{workspace.get_memory_db_path()}"
        
        # Create and store agent
        agent = AgentCore(settings)
        workspace.agent = agent
        
        logger.info(f"Created agent for workspace: {workspace.name}")
        return agent
    
    def get_or_create_agent(
        self, 
        workspace_id: str,
        base_settings: Optional[Settings] = None
    ) -> Optional[AgentCore]:
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

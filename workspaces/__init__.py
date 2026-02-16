"""Multi-agent workspace management"""

from .workspace import Workspace, WorkspaceConfig, WORKSPACE_TEMPLATES
from .manager import WorkspaceManager

__all__ = ["Workspace", "WorkspaceConfig", "WorkspaceManager", "WORKSPACE_TEMPLATES"]

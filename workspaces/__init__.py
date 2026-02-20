"""Multi-agent workspace management"""

from .workspace import Workspace, WorkspaceConfig, WORKSPACE_TEMPLATES

def __getattr__(name: str):
    """Lazy-load WorkspaceManager to avoid circular import with agent.core."""
    if name == "WorkspaceManager":
        from .manager import WorkspaceManager
        return WorkspaceManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Workspace", "WorkspaceConfig", "WorkspaceManager", "WORKSPACE_TEMPLATES"]

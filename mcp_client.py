"""
MCP client for calling tools on configured MCP servers.
Supports local (stdio) and remote (HTTP) servers.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MCP_AVAILABLE = False
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    MCP_AVAILABLE = True
except ImportError:
    pass

STREAMABLE_HTTP_AVAILABLE = False
try:
    from mcp.client.streamable_http import streamable_http_client

    STREAMABLE_HTTP_AVAILABLE = True
except ImportError:
    pass

# Cache for discovered tools: (mcp_file_path, mtime) -> {server_name: [(tool_name, description), ...]}
_tools_cache: Dict[Tuple[str, float], Dict[str, List[Tuple[str, str]]]] = {}


def _get_expanded_env() -> Dict[str, str]:
    """Expand PATH for macOS GUI apps that don't inherit shell env."""
    env = os.environ.copy()
    current = env.get("PATH", "")
    extra = [
        "/opt/homebrew/bin",
        "/opt/homebrew/sbin",
        "/usr/local/bin",
        "/usr/local/sbin",
        str(Path.home() / ".local" / "bin"),
        str(Path.home() / ".cargo" / "bin"),
        "/usr/bin",
        "/bin",
    ]
    for p in extra:
        if os.path.isdir(p) and p not in current:
            current = f"{p}:{current}"
    env["PATH"] = current
    return env


def _load_server_config(mcp_file: Path, mcp_name: str) -> Optional[Dict[str, Any]]:
    """Load server config from mcpServers JSON."""
    if not mcp_file.exists():
        return None
    try:
        with open(mcp_file, "r") as f:
            data = json.load(f)
        servers = data.get("mcpServers", {})
        return servers.get(mcp_name)
    except Exception as e:
        logger.warning(f"Failed to load MCP config: {e}")
        return None


def _load_all_servers(mcp_file: Path) -> Dict[str, Dict[str, Any]]:
    """Load all server configs from mcpServers JSON."""
    if not mcp_file.exists():
        return {}
    try:
        with open(mcp_file, "r") as f:
            data = json.load(f)
        return data.get("mcpServers", {})
    except Exception as e:
        logger.warning(f"Failed to load MCP config: {e}")
        return {}


async def _list_tools_stdio(config: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Connect via stdio, list tools, return [(name, description), ...]."""
    cmd = config.get("command", "")
    args_list = config.get("args", [])
    if isinstance(args_list, str):
        args_list = args_list.split() if args_list else []
    elif not isinstance(args_list, (list, tuple)):
        args_list = []
    if not cmd:
        return []
    server_params = StdioServerParameters(
        command=cmd,
        args=[str(a) for a in args_list],
        env=_get_expanded_env(),
    )
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_result = await session.list_tools()
                out = []
                for t in getattr(tools_result, "tools", []):
                    name = getattr(t, "name", str(t))
                    desc = getattr(t, "description", "") or ""
                    out.append((name, desc))
                return out
    except Exception as e:
        logger.debug(f"Tool discovery failed (stdio): {e}")
        return []


async def _list_tools_http(config: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Connect via streamable HTTP, list tools, return [(name, description), ...]."""
    if not STREAMABLE_HTTP_AVAILABLE:
        return []
    mcp_url = _get_mcp_url(config)
    if not mcp_url:
        return []
    headers = config.get("headers") or {}
    if isinstance(headers, str):
        try:
            headers = json.loads(headers) if headers else {}
        except json.JSONDecodeError:
            headers = {}
    try:
        import httpx

        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(15.0),
        ) as http_client:
            async with streamable_http_client(mcp_url, http_client=http_client) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools_result = await session.list_tools()
                    out = []
                    for t in getattr(tools_result, "tools", []):
                        name = getattr(t, "name", str(t))
                        desc = getattr(t, "description", "") or ""
                        out.append((name, desc))
                    return out
    except Exception as e:
        logger.debug(f"Tool discovery failed (http): {e}")
        return []


def _get_mcp_url(config: Dict[str, Any]) -> str:
    """Build MCP endpoint URL from config."""
    url = config.get("url", "").rstrip("/")
    if not url:
        return ""
    if url.endswith("/mcp") or url.endswith("/mcp/"):
        return url.rstrip("/")
    return f"{url.rstrip('/')}/mcp"


async def _call_tool_http(
    config: Dict[str, Any], tool_name: str, arguments: Dict[str, Any]
) -> str:
    """Call MCP tool via streamable HTTP. Returns result text or error string."""
    if not STREAMABLE_HTTP_AVAILABLE:
        return "**❌ Remote MCP requires mcp package with streamable HTTP support.**"
    mcp_url = _get_mcp_url(config)
    if not mcp_url:
        return "**❌ Invalid URL in MCP server config.**"
    headers = config.get("headers") or {}
    if isinstance(headers, str):
        try:
            headers = json.loads(headers) if headers else {}
        except json.JSONDecodeError:
            headers = {}
    try:
        import httpx

        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(30.0),
        ) as http_client:
            async with streamable_http_client(mcp_url, http_client=http_client) as (
                read,
                write,
                _,
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments)

                    if getattr(result, "isError", False):
                        err_parts = []
                        for c in result.content:
                            if hasattr(c, "text"):
                                err_parts.append(c.text)
                        return f"**❌ Tool error:** {' '.join(err_parts) or 'Unknown error'}"

                    parts = []
                    for content in result.content:
                        if hasattr(content, "text"):
                            parts.append(content.text)
                    return "\n".join(parts) if parts else "(No output)"
    except Exception as e:
        logger.exception(f"MCP HTTP tool call failed: {tool_name}")
        return f"**❌ Tool error:** {str(e)}"


async def discover_tools(mcp_file: Path) -> Dict[str, List[Tuple[str, str]]]:
    """
    Discover tools from all configured MCP servers.
    Returns {server_name: [(tool_name, description), ...]}.
    Cached by mcp_file path and mtime.
    """
    if not MCP_AVAILABLE:
        return {}
    path_str = str(mcp_file.resolve())
    try:
        mtime = mcp_file.stat().st_mtime
    except OSError:
        mtime = 0
    cache_key = (path_str, mtime)
    if cache_key in _tools_cache:
        return _tools_cache[cache_key]
    servers = _load_all_servers(mcp_file)
    result: Dict[str, List[Tuple[str, str]]] = {}
    for name, config in servers.items():
        if "url" in config:
            tools = await _list_tools_http(config)
        else:
            tools = await _list_tools_stdio(config)
        if tools:
            result[name] = tools
    _tools_cache[cache_key] = result
    return result


async def call_mcp_tool(
    mcp_file: Path,
    mcp_name: str,
    tool_name: str,
    arguments: Dict[str, Any],
) -> str:
    """
    Call an MCP tool. Spawns the server via stdio, calls the tool, returns result text.
    Returns error message string on failure.
    """
    if not MCP_AVAILABLE:
        return "**❌ MCP client not available.** Install with: pip install mcp"

    config = _load_server_config(mcp_file, mcp_name)
    if not config:
        return f"**❌ MCP server '{mcp_name}' not found in config.**"

    if "url" in config:
        return await _call_tool_http(config, tool_name, arguments)

    cmd = config.get("command", "")
    args_list = config.get("args", [])
    if isinstance(args_list, str):
        args_list = args_list.split() if args_list else []
    elif not isinstance(args_list, (list, tuple)):
        args_list = []

    if not cmd:
        return f"**❌ No command for MCP server '{mcp_name}'.**"

    server_params = StdioServerParameters(
        command=cmd,
        args=[str(a) for a in args_list],
        env=_get_expanded_env(),
    )

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)

                if getattr(result, "isError", False):
                    err_parts = []
                    for c in result.content:
                        if hasattr(c, "text"):
                            err_parts.append(c.text)
                    return f"**❌ Tool error:** {' '.join(err_parts) or 'Unknown error'}"

                parts = []
                for content in result.content:
                    if hasattr(content, "text"):
                        parts.append(content.text)
                return "\n".join(parts) if parts else "(No output)"
    except FileNotFoundError as e:
        return f"**❌ Command not found:** {cmd}. Ensure it's installed and in PATH."
    except Exception as e:
        logger.exception(f"MCP tool call failed: {mcp_name}.{tool_name}")
        return f"**❌ Tool error:** {str(e)}"

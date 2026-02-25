"""
MCP client for calling tools on configured MCP servers.
Supports local (stdio) and remote (HTTP) servers.
"""
from __future__ import annotations

import asyncio
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

# Default timeout for a single tool call (seconds)
DEFAULT_TOOL_CALL_TIMEOUT = 60

# Per-server discovery timeout (seconds)
DISCOVERY_SERVER_TIMEOUT = 10


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


def _env_for_server(config: Dict[str, Any]) -> Dict[str, str]:
    """Base env plus per-server env from config (so API keys, PORT, etc. are used in tool calls)."""
    env = _get_expanded_env()
    server_env = config.get("env") or {}
    if isinstance(server_env, dict):
        for k, v in server_env.items():
            env[str(k)] = str(v)
    return env


def normalize_mcp_args(args: Any) -> List[str]:
    """Normalize MCP server args to a flat list of strings. Handles args stored as a
    JSON array string (e.g. '[\"mcp-macos\"]') so npx receives mcp-macos, not the literal string.
    """
    if args is None:
        return []
    if isinstance(args, str):
        s = args.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
                return [s]
            except json.JSONDecodeError:
                return s.split() if s else []
        return s.split() if s else []
    if not isinstance(args, (list, tuple)):
        return []
    out: List[str] = []
    for a in args:
        s = str(a).strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    out.extend(str(x) for x in parsed)
                else:
                    out.append(s)
            except json.JSONDecodeError:
                out.append(s)
        else:
            out.append(s)
    return out


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
    args_list = normalize_mcp_args(config.get("args", []))
    if not cmd:
        return []
    server_params = StdioServerParameters(
        command=cmd,
        args=[str(a) for a in args_list],
        env=_env_for_server(config),
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
        err_msg = str(e)
        if hasattr(e, "exceptions") and e.exceptions:
            sub = e.exceptions[0]
            err_msg = f"{type(sub).__name__}: {sub}"
        return f"**❌ Tool error:** {err_msg}"


def invalidate_tools_cache(mcp_file: Optional[Path] = None) -> None:
    """Invalidate discovery cache so the next discover_tools refetches. If mcp_file is None, clear all."""
    global _tools_cache
    if mcp_file is None:
        _tools_cache.clear()
        return
    path_str = str(mcp_file.resolve())
    to_drop = [k for k in _tools_cache if k[0] == path_str]
    for k in to_drop:
        del _tools_cache[k]


async def _discover_one(
    name: str, config: Dict[str, Any]
) -> Tuple[str, List[Tuple[str, str]]]:
    """Discover tools for one server; returns (server_name, tools). Used for parallel discovery."""
    if "url" in config:
        tools = await _list_tools_http(config)
    else:
        tools = await _list_tools_stdio(config)
    return (name, tools)


async def discover_tools(
    mcp_file: Path, force_refresh: bool = False
) -> Dict[str, List[Tuple[str, str]]]:
    """
    Discover tools from all configured MCP servers.
    Returns {server_name: [(tool_name, description), ...]}.
    Cached by mcp_file path and mtime unless force_refresh is True.
    Runs servers in parallel with per-server timeout.
    """
    if not MCP_AVAILABLE:
        return {}
    path_str = str(mcp_file.resolve())
    try:
        mtime = mcp_file.stat().st_mtime
    except OSError:
        mtime = 0
    cache_key = (path_str, mtime)
    if not force_refresh and cache_key in _tools_cache:
        return _tools_cache[cache_key]
    servers = _load_all_servers(mcp_file)
    if not servers:
        _tools_cache[cache_key] = {}
        return {}

    async def one_with_timeout(name: str, config: Dict[str, Any]) -> Tuple[str, List[Tuple[str, str]]]:
        try:
            return await asyncio.wait_for(
                _discover_one(name, config), timeout=DISCOVERY_SERVER_TIMEOUT
            )
        except asyncio.TimeoutError:
            logger.warning("MCP discovery timed out for server: %s", name)
            return (name, [])
        except Exception as e:
            logger.debug("MCP discovery failed for %s: %s", name, e)
            return (name, [])

    tasks = [one_with_timeout(name, config) for name, config in servers.items()]
    results = await asyncio.gather(*tasks, return_exceptions=False)
    result: Dict[str, List[Tuple[str, str]]] = {}
    for name, tools in results:
        if tools:
            result[name] = tools
    _tools_cache[cache_key] = result
    return result


async def discover_one_server(
    mcp_file: Path, server_name: str
) -> Tuple[List[Tuple[str, str]], Optional[str]]:
    """
    Discover tools for a single MCP server. For use by Settings "Test this server".
    Returns (list of (tool_name, description), error_message).
    If error_message is set, tools may be empty.
    """
    if not MCP_AVAILABLE:
        return [], "MCP client not available"
    config = _load_server_config(mcp_file, server_name)
    if not config:
        return [], f"Server '{server_name}' not found in config"
    try:
        if "url" in config:
            tools = await asyncio.wait_for(
                _list_tools_http(config), timeout=DISCOVERY_SERVER_TIMEOUT
            )
        else:
            tools = await asyncio.wait_for(
                _list_tools_stdio(config), timeout=DISCOVERY_SERVER_TIMEOUT
            )
        return tools, None
    except asyncio.TimeoutError:
        return [], f"Discovery timed out (>{DISCOVERY_SERVER_TIMEOUT}s)"
    except FileNotFoundError as e:
        return [], f"Command not found: {e}"
    except Exception as e:
        err = str(e)
        if hasattr(e, "exceptions") and e.exceptions:
            err = f"{type(e.exceptions[0]).__name__}: {e.exceptions[0]}"
        return [], err


async def validate_server_config(config: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Validate MCP server config (e.g. before save in Add/Edit dialog).
    Local: try listing tools via stdio; remote: try HTTP connection.
    Returns (True, "OK (N tools)") or (False, error_message).
    """
    if not MCP_AVAILABLE:
        return False, "MCP client not available"
    if config.get("url"):
        if not STREAMABLE_HTTP_AVAILABLE:
            return False, "Remote MCP requires streamable HTTP support"
        url = _get_mcp_url(config)
        if not url:
            return False, "Invalid URL"
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(url)
                if r.status_code in (200, 404, 405):
                    return True, "Remote server reachable"
                return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)
    cmd = config.get("command", "")
    if not cmd:
        return False, "No command set"
    try:
        tools = await asyncio.wait_for(
            _list_tools_stdio(config), timeout=DISCOVERY_SERVER_TIMEOUT
        )
        return True, f"OK — {len(tools)} tools" if tools else (False, "No tools returned")
    except asyncio.TimeoutError:
        return False, f"Timed out (>{DISCOVERY_SERVER_TIMEOUT}s)"
    except FileNotFoundError:
        return False, f"Command not found: {cmd}"
    except Exception as e:
        err = str(e)
        if hasattr(e, "exceptions") and e.exceptions:
            err = f"{type(e.exceptions[0]).__name__}: {e.exceptions[0]}"
        return False, err


async def health_check_servers(mcp_file: Path) -> Dict[str, bool]:
    """
    Probe each configured MCP server; return {server_name: True/False}.
    Used for background health checks and UI status. Failed servers can be retried
    on next discovery (cache invalidation triggers refetch).
    """
    if not MCP_AVAILABLE:
        return {}
    servers = _load_all_servers(mcp_file)
    if not servers:
        return {}
    result: Dict[str, bool] = {}

    async def probe(name: str, config: Dict[str, Any]) -> Tuple[str, bool]:
        try:
            if "url" in config:
                await asyncio.wait_for(_list_tools_http(config), timeout=DISCOVERY_SERVER_TIMEOUT)
            else:
                await asyncio.wait_for(_list_tools_stdio(config), timeout=DISCOVERY_SERVER_TIMEOUT)
            return (name, True)
        except Exception:
            return (name, False)

    tasks = [probe(name, config) for name, config in servers.items()]
    done = await asyncio.gather(*tasks, return_exceptions=True)
    for d in done:
        if isinstance(d, Exception):
            logger.debug("MCP health check error: %s", d)
            continue
        name, ok = d
        result[name] = ok
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
        return (
            f"**❌ MCP server '{mcp_name}' not found.** "
            "Add it in Settings → Skills & MCP, or check the server name."
        )

    if "url" in config:
        return await _call_tool_http(config, tool_name, arguments)

    cmd = config.get("command", "")
    args_list = normalize_mcp_args(config.get("args", []))

    if not cmd:
        return f"**❌ No command for MCP server '{mcp_name}'.**"

    server_params = StdioServerParameters(
        command=cmd,
        args=args_list,
        env=_env_for_server(config),
    )

    async def _run_stdio_call() -> str:
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

    async def _call_with_retry() -> str:
        try:
            return await asyncio.wait_for(_run_stdio_call(), timeout=DEFAULT_TOOL_CALL_TIMEOUT)
        except FileNotFoundError:
            raise
        except (asyncio.TimeoutError, ConnectionError, OSError) as e:
            logger.warning("MCP transient error (will retry once): %s", e)
            await asyncio.sleep(1.5)
            return await asyncio.wait_for(_run_stdio_call(), timeout=DEFAULT_TOOL_CALL_TIMEOUT)
        except Exception as e:
            if "timeout" in str(e).lower() or "connection" in str(e).lower():
                logger.warning("MCP transient error (will retry once): %s", e)
                await asyncio.sleep(1.5)
                return await asyncio.wait_for(_run_stdio_call(), timeout=DEFAULT_TOOL_CALL_TIMEOUT)
            raise

    try:
        return await _call_with_retry()
    except asyncio.TimeoutError:
        return f"**❌ MCP tool call timed out** (>{DEFAULT_TOOL_CALL_TIMEOUT}s). Server '{mcp_name}' may be stuck."
    except FileNotFoundError:
        base = cmd.split("/")[-1].split()[0] if cmd else "command"
        return (
            f"**❌ Command not found:** {cmd}. "
            f"Install the required runtime (e.g. Node.js for npx) or set Environment for this server in Settings → Skills & MCP."
        )
    except Exception as e:
        logger.exception(f"MCP tool call failed: {mcp_name}.{tool_name}")
        err_msg = str(e)
        if hasattr(e, "exceptions") and e.exceptions:
            sub = e.exceptions[0]
            err_msg = f"{type(sub).__name__}: {sub}"
        return f"**❌ Tool error:** {err_msg}"


def discover_mcp_servers_zeroconf(timeout_seconds: float = 5.0) -> List[Dict[str, Any]]:
    """
    Discover MCP servers on the local network via mDNS (ZeroConf).
    Servers that advertise _mcp._tcp.local. will be listed.
    Returns list of {"name": str, "host": str, "port": int}; empty if zeroconf not installed or none found.
    """
    try:
        from zeroconf import Zeroconf, ServiceListener, ServiceBrowser
    except ImportError:
        logger.debug("zeroconf not installed; skipping MCP network discovery")
        return []

    results: List[Dict[str, Any]] = []

    class MCPListener(ServiceListener):
        def add_service(self, zc: Any, type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name)
            if not info:
                return
            host = info.server or name
            if info.parsed_addresses:
                host = info.parsed_addresses[0]
            port = info.port or 0
            if port:
                results.append({"name": name.replace("._mcp._tcp.local.", ""), "host": host, "port": port})

    try:
        import time
        zc = Zeroconf()
        listener = MCPListener()
        ServiceBrowser(zc, "_mcp._tcp.local.", listener)
        time.sleep(timeout_seconds)
        zc.close()
    except Exception as e:
        logger.debug("Zeroconf discovery error: %s", e)
    return results

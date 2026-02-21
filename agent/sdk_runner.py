"""OpenAI Agents SDK + LiteLLM runner for improved coding workflows.

When use_agents_sdk is enabled on a workspace, this module runs the agent
via the SDK instead of the custom tool loop. Preserves memory, MCP, and
multi-provider support (Ollama, LM Studio, OpenAI, Anthropic via LiteLLM).

Imports of LitellmModel and MCP are deferred until run_agents_sdk is called,
so the bundled app does not load LiteLLM at startup (which fails when
model_prices_and_context_window_backup.json is missing in the bundle).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from grizzyclaw.agent.command_parsers import (
    extract_balanced_brace,
    extract_balanced_brace_dumb,
    normalize_llm_json,
)

logger = logging.getLogger(__name__)

_MAX_500_RETRIES = 2


# Tool name aliases: LLM often outputs wrong names; map to actual fast-filesystem tools.
# Covers efforthye/fast-filesystem-mcp and similar MCPs. Add aliases for common typos/hallucinations.
_TOOL_NAME_ALIASES: Dict[str, str] = {
    # fast_create_directory
    "fast_createdirectory": "fast_create_directory",
    "fast_make_dir": "fast_create_directory",
    "fast_make_directories": "fast_create_directory",
    "fast_create_dir": "fast_create_directory",
    "fast_create_dirs": "fast_create_directory",
    "create_directory": "fast_create_directory",
    "make_directory": "fast_create_directory",
    # fast_list_allowed_directories
    "fast_list_allowed_DIRECTORIES": "fast_list_allowed_directories",
    "fast_list_allowed_dirs": "fast_list_allowed_directories",
    "fast_list_allowed_dir": "fast_list_allowed_directories",
    "list_allowed_directories": "fast_list_allowed_directories",
    # fast_list_directory
    "fast_list_directories": "fast_list_directory",
    "fast_list_dir": "fast_list_directory",
    "fast_list_dirs": "fast_list_directory",
    "list_directory": "fast_list_directory",
    # fast_write_file
    "fast_write": "fast_write_file",
    "write_file": "fast_write_file",
    "fast_write_files": "fast_write_file",
    # fast_read_file
    "fast_read": "fast_read_file",
    "read_file": "fast_read_file",
    "fast_read_files": "fast_read_file",
    # fast_edit_block (old_text/new_text replacement)
    "fast_replace_file": "fast_edit_block",
    "fast_replace": "fast_edit_block",
    "fast_search_replace": "fast_edit_block",
    "fast_find_replace": "fast_edit_block",
    "replace_file": "fast_edit_block",
    "edit_block": "fast_edit_block",
    # fast_edit_file (line-based editing)
    "fast_edit": "fast_edit_file",
    "edit_file": "fast_edit_file",
    # fast_edit_blocks / fast_edit_multiple_blocks
    "fast_edit_multiple_block": "fast_edit_multiple_blocks",
    "fast_edit_blocks_batch": "fast_edit_blocks",
    # fast_delete_file
    "fast_delete": "fast_delete_file",
    "delete_file": "fast_delete_file",
    # fast_copy_file
    "fast_copy": "fast_copy_file",
    "copy_file": "fast_copy_file",
    # fast_move_file
    "fast_move": "fast_move_file",
    "move_file": "fast_move_file",
    "fast_rename": "fast_move_file",
    # fast_search_files
    "fast_search": "fast_search_files",
    "fast_search_file": "fast_search_files",
    "search_files": "fast_search_files",
    # fast_search_and_replace (regex search/replace)
    "fast_search_replace_regex": "fast_search_and_replace",
    "fast_regex_replace": "fast_search_and_replace",
    # fast_get_file_info
    "fast_file_info": "fast_get_file_info",
    "get_file_info": "fast_get_file_info",
    # fast_get_directory_tree
    "fast_directory_tree": "fast_get_directory_tree",
    "fast_list_tree": "fast_get_directory_tree",
    "get_directory_tree": "fast_get_directory_tree",
}


def _repair_unescaped_newlines_in_strings(raw: str) -> str:
    """Escape literal newlines/carriage returns inside JSON string values."""
    def repl(m: re.Match[str]) -> str:
        content = m.group(1)
        content = content.replace("\r", "\\r").replace("\n", "\\n")
        return '"' + content + '"'
    return re.sub(r'"((?:[^"\\]|\\.)*)"', repl, raw)


def _repair_unquoted_keys(raw: str) -> str:
    """Wrap unquoted JSON keys in quotes: {path: "/x"} -> {"path": "/x"}."""
    return re.sub(
        r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:',
        r'\1"\2":',
        raw,
    )


def _extract_first_json_object(raw: str) -> str:
    """When multiple objects are concatenated (}{), extract and return the first."""
    idx = raw.find("{")
    if idx < 0:
        return raw
    pair = extract_balanced_brace(raw, idx)
    if pair is None:
        pair = extract_balanced_brace_dumb(raw, idx)
    if pair:
        return raw[pair[0] : pair[1]]
    return raw


def _try_parse_json_args(raw: str) -> Optional[Dict[str, Any]]:
    """Parse JSON string, with repair for common LLM malformed output.

    Uses normalize_llm_json (same as non-SDK path) for smart quotes, trailing
    commas, backslash fixes, etc., then SDK-specific repairs for unescaped
    newlines, recursive key quirks, unquoted keys, and concatenated objects.
    """
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Concatenated objects: {"a":1}{"b":2} -> extract first only
    extracted = _extract_first_json_object(raw)
    if extracted != raw:
        try:
            return json.loads(extracted)
        except json.JSONDecodeError:
            raw = extracted
    # Align with non-SDK path: apply full normalize_llm_json (smart quotes,
    # trailing commas, backslash fixes, code fences, etc.)
    repaired = normalize_llm_json(raw)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    # JavaScript values invalid in JSON: undefined -> null, NaN -> null
    repaired = re.sub(r"\bundefined\b", "null", repaired)
    repaired = re.sub(r"\bNaN\b", "null", repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    # Unquoted keys: {path: "/x"} -> {"path": "/x"}
    repaired = _repair_unquoted_keys(repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    # SDK-specific: "recursive\": true -> "recursive": true
    repaired = repaired.replace('\\":', '":').replace('\\": ', '": ')
    repaired = repaired.replace('"recursive\\"', '"recursive"').replace('"recursive\\\\"', '"recursive"')
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    # Repair unescaped newlines in string values (LLM sometimes emits literal newlines)
    repaired = _repair_unescaped_newlines_in_strings(repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    return None


def _normalize_mcp_args_keys(args: Dict[str, Any]) -> Dict[str, Any]:
    """Fix keys that LLM outputs with typos or malformed JSON (trailing quote, etc.)."""
    out: Dict[str, Any] = {}
    for k, v in args.items():
        # Strip trailing quote/backslash from keys (e.g. recursive" from "recursive\":)
        clean = k.rstrip('"\'\\')
        if k in ("new_test", "old_test"):
            clean = "new_text" if k == "new_test" else "old_text"
        elif clean == "<!--content-->":
            clean = "content"
        # Prefer existing clean key's value; otherwise use this one
        if clean not in out:
            out[clean] = v
    return out


def _coerce_mcp_tool_args(args: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Coerce common LLM output mistakes before passing to MCP tools."""
    if args is None:
        return None
    if isinstance(args, str):
        args = _try_parse_json_args(args)
        if args is None:
            return None
    if not isinstance(args, dict):
        return args
    out = _normalize_mcp_args_keys(args)
    # Boolean params: "true"/"false" string -> bool (MCP tools expect bool)
    _BOOL_PARAMS = frozenset((
        "recursive", "create_dirs", "create_directories", "backup", "overwrite",
        "use_regex", "create_backup",
    ))
    for key in _BOOL_PARAMS:
        if key in out:
            v = out[key]
            if isinstance(v, str):
                out[key] = v.lower() in ("true", "1", "yes")
    # path: /users/ -> /Users/ on macOS (case-sensitive)
    if "path" in out and isinstance(out["path"], str):
        p = out["path"]
        if p.startswith("/users/"):
            out["path"] = "/Users/" + p[7:]
    return out


def _format_exception_for_display(exc: BaseException) -> str:
    """Unwrap ExceptionGroup to show the actual cause, matching mcp_client behavior."""
    if hasattr(exc, "exceptions") and exc.exceptions:
        sub = exc.exceptions[0]
        return f"{type(sub).__name__}: {sub}"
    return str(exc)


def _wrap_mcp_server_with_arg_coercion(server: Any) -> Any:
    """Wrap MCP server to coerce tool args and support tool name aliases."""

    class _CoercingWrapper:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        async def list_tools(
            self,
            run_context: Any = None,
            agent: Any = None,
        ) -> List[Any]:
            tools = await self._inner.list_tools(run_context, agent)
            if not tools:
                return tools
            out = list(tools)
            # Add alias tools so model's wrong names (fast_createdirectory, etc.) resolve
            real_to_aliases: Dict[str, List[str]] = {}
            for alias, real in _TOOL_NAME_ALIASES.items():
                real_to_aliases.setdefault(real, []).append(alias)
            for tool in tools:
                tool_name = getattr(tool, "name", None)
                if tool_name and tool_name in real_to_aliases:
                    for alias in real_to_aliases[tool_name]:
                        try:
                            alias_tool = tool.model_copy(update={"name": alias})
                            out.append(alias_tool)
                        except Exception:
                            pass
            return out

        async def call_tool(
            self,
            tool_name: str,
            arguments: Optional[Dict[str, Any]],
            meta: Optional[Dict[str, Any]] = None,
        ) -> Any:
            resolved_name = _TOOL_NAME_ALIASES.get(tool_name, tool_name)
            coerced = _coerce_mcp_tool_args(arguments)
            try:
                return await self._inner.call_tool(resolved_name, coerced, meta)
            except BaseException as e:
                # Unwrap ExceptionGroup so user sees the real error (e.g. path not allowed)
                if hasattr(e, "exceptions") and e.exceptions:
                    raise e.exceptions[0] from e
                raise

    return _CoercingWrapper(server)

# Do NOT import agents/litellm at module load - LiteLLM loads a JSON file
# that may be missing in PyInstaller bundles, causing startup crash.
# Availability is checked lazily when run_agents_sdk is called.
AGENTS_SDK_AVAILABLE = True  # Assume available; ImportError handled in run_agents_sdk


def _get_api_key(settings: Any, workspace: Any, key_attr: str) -> str:
    """Prefer workspace API key override over global settings."""
    if workspace and getattr(workspace, key_attr, None):
        return str(getattr(workspace, key_attr))
    return str(getattr(settings, key_attr, "") or "")


def _ensure_litellm_cost_map_in_bundle() -> None:
    """Create minimal cost map JSON if missing (PyInstaller bundles sometimes omit it)."""
    import os
    import sys

    def _try_create(path: str) -> bool:
        if os.path.exists(path):
            return True
        parent = os.path.dirname(path)
        try:
            os.makedirs(parent, exist_ok=True)
            with open(path, "w") as f:
                f.write("{}")
            logger.debug("Created minimal litellm cost map at %s", path)
            return True
        except OSError:
            return False

    if not getattr(sys, "frozen", False):
        return
    # PyInstaller/macOS: litellm looks in Contents/Frameworks/litellm/ for the JSON
    candidates = [
        os.path.join(sys._MEIPASS, "litellm", "model_prices_and_context_window_backup.json")
        if hasattr(sys, "_MEIPASS")
        else None,
        # Mac .app: executable is in .../GrizzyClaw.app/Contents/MacOS/
        (
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(sys.executable))),
                "Frameworks",
                "litellm",
                "model_prices_and_context_window_backup.json",
            )
            if sys.executable
            else None
        ),
    ]
    for path in candidates:
        if path and _try_create(path):
            return


def _configure_litellm_for_sdk() -> None:
    """Work around LiteLLM chunk builder errors (Ollama/local providers). Patch at main module."""
    try:
        # Pre-load tiktoken encoding so LiteLLM's usage calculation can find cl100k_base
        # (PyInstaller bundles may not discover tiktoken_ext.openai_public automatically)
        try:
            import tiktoken_ext.openai_public  # noqa: F401
        except ImportError:
            pass
        import litellm

        litellm.turn_off_message_logging = True
        # Patch stream_chunk_builder at source; avoid touching litellm.stream_chunk_builder
        # (which can trigger 'has no attribute types' in some environments).
        from litellm import main as _litellm_main

        _orig_builder = _litellm_main.stream_chunk_builder

        def _safe_builder(*args: Any, **kwargs: Any) -> Any:
            try:
                return _orig_builder(*args, **kwargs)
            except Exception as e:
                logger.debug("LiteLLM stream_chunk_builder failed (non-fatal): %s", e)
                return None

        _litellm_main.stream_chunk_builder = _safe_builder
        # Also patch litellm.stream_chunk_builder (used by streaming_handler, logging_utils)
        litellm.stream_chunk_builder = _safe_builder
    except Exception:
        pass


def _get_litellm_model(
    provider: str,
    model: str,
    settings: Any,
    workspace: Optional[Any] = None,
) -> Optional[Any]:
    """Build LitellmModel from GrizzyClaw provider/model and settings."""
    _ensure_litellm_cost_map_in_bundle()
    try:
        from agents.extensions.models.litellm_model import LitellmModel

        _configure_litellm_for_sdk()
    except (ImportError, FileNotFoundError) as e:
        logger.debug("LitellmModel import failed: %s", e)
        return None
    model = (model or "").strip() or "llama3.2"
    provider = (provider or "ollama").lower()

    api_key = "sk-1234"  # LiteLLM placeholder for local providers
    base_url: Optional[str] = None

    if provider == "ollama":
        litellm_model = f"ollama/{model}"
        url = getattr(settings, "ollama_url", None) or ""
        base_url = url.strip() or "http://localhost:11434"
    elif provider == "lmstudio":
        litellm_model = f"openai/{model}"
        # Use main Settings only (matches normal chat path; Preferences → LLM Providers)
        url = getattr(settings, "lmstudio_url", None) or ""
        base_url = url.strip() or "http://localhost:1234/v1"
        api_key = "sk-1234"
    elif provider == "openai":
        litellm_model = f"openai/{model}"
        api_key = _get_api_key(settings, workspace, "openai_api_key")
        if not api_key:
            logger.warning("OpenAI API key not set for Agents SDK")
            return None
    elif provider == "anthropic":
        litellm_model = f"anthropic/{model}"
        api_key = _get_api_key(settings, workspace, "anthropic_api_key")
        if not api_key:
            logger.warning("Anthropic API key not set for Agents SDK")
            return None
    elif provider == "openrouter":
        litellm_model = f"openrouter/{model}"
        api_key = _get_api_key(settings, workspace, "openrouter_api_key")
        if not api_key:
            logger.warning("OpenRouter API key not set for Agents SDK")
            return None
    else:
        litellm_model = f"ollama/{model}"

    kwargs: Dict[str, Any] = {"model": litellm_model, "api_key": api_key}
    if base_url:
        base_url = str(base_url).rstrip("/")
        kwargs["base_url"] = base_url
    return LitellmModel(**kwargs)


def _load_mcp_server_instances(mcp_file: Path) -> List[Any]:
    """Load MCP server instances from grizzyclaw.json for SDK."""
    if not mcp_file.exists():
        return []
    try:
        from agents.mcp import MCPServerManager, MCPServerStdio, MCPServerStreamableHttp
    except ImportError:
        return []
    servers: List[Any] = []
    try:
        with open(mcp_file, "r") as f:
            data = json.load(f)
        mcp_servers = data.get("mcpServers", {}) or data.get("mcp_servers", {})
        for name, cfg in mcp_servers.items():
            if not isinstance(cfg, dict):
                continue
            if "url" in cfg:
                url = str(cfg.get("url", "")).rstrip("/")
                if url.startswith("http"):
                    if not url.endswith("/mcp"):
                        url = f"{url}/mcp" if not url.endswith("/") else f"{url}mcp"
                    try:
                        server = MCPServerStreamableHttp(
                            name=name,
                            params={"url": url, "timeout": 30},
                        )
                        servers.append(_wrap_mcp_server_with_arg_coercion(server))
                    except Exception as e:
                        logger.debug("Skip MCP HTTP server %s: %s", name, e)
            else:
                cmd = cfg.get("command", "")
                args = cfg.get("args", [])
                if isinstance(args, str):
                    args = args.split() if args else []
                # fast-filesystem-mcp: inject --allow for user home and common paths if not present
                if name == "fast-filesystem" and cmd and "fast-filesystem" in " ".join(
                    str(a).lower() for a in args
                ):
                    home = str(Path.home())
                    existing = [
                        args[i + 1]
                        for i, a in enumerate(args)
                        if a == "--allow" and i + 1 < len(args)
                    ]
                    to_add = [
                        p
                        for p in (
                            home,
                            str(Path(home) / "ToDo"),
                            str(Path(home) / "Projects"),
                            "/Volumes/Storage/Projects",
                        )
                        if p and p not in existing
                    ]
                    for p in to_add:
                        args = list(args) + ["--allow", p]
                if cmd:
                    try:
                        server = MCPServerStdio(
                            name=name,
                            params={"command": cmd, "args": [str(a) for a in args]},
                        )
                        servers.append(_wrap_mcp_server_with_arg_coercion(server))
                    except Exception as e:
                        logger.debug("Skip MCP stdio server %s: %s", name, e)
    except Exception as e:
        logger.warning("Failed to load MCP config for SDK: %s", e)
    return servers


async def run_agents_sdk(
    message: str,
    system_prompt: str,
    memory_context: str,
    provider: str,
    model: str,
    temperature: float,
    max_tokens: int,
    settings: Any,
    mcp_file: Path,
    workspace: Optional[Any] = None,
    max_turns: int = 25,
) -> AsyncIterator[str]:
    """Run the OpenAI Agents SDK with LiteLLM support. Yields text chunks."""
    if not AGENTS_SDK_AVAILABLE:
        yield "**Agents SDK not available.** Install with: pip install 'openai-agents[litellm]'"
        return

    # Defer LitellmModel import until here so bundled app doesn't load LiteLLM at startup
    llm_model = _get_litellm_model(provider, model, settings, workspace)
    if not llm_model:
        yield "**Could not configure model for Agents SDK.** Check API keys and provider settings."
        return

    instructions = system_prompt
    if memory_context:
        instructions += memory_context

    mcp_instances = _load_mcp_server_instances(mcp_file)

    try:
        from openai.types.responses import ResponseTextDeltaEvent
    except ImportError:
        ResponseTextDeltaEvent = None  # type: ignore[misc, assignment]

    def _extract_delta(event: Any) -> Optional[str]:
        if event.type != "raw_response_event" or event.data is None:
            return None
        data = event.data
        if ResponseTextDeltaEvent and isinstance(data, ResponseTextDeltaEvent):
            delta = getattr(data, "delta", None)
            return delta if isinstance(delta, str) else None
        if hasattr(data, "delta"):
            delta = getattr(data, "delta", None)
            return delta if isinstance(delta, str) else None
        return None

    try:
        from agents import Agent, ModelSettings, Runner
    except ImportError:
        yield "**Agents SDK not available.** Install with: pip install 'openai-agents[litellm]'"
        return

    async def _run_once() -> None:
        if mcp_instances:
            from agents.mcp import MCPServerManager
            async with MCPServerManager(mcp_instances) as manager:
                agent = Agent(
                    name="Assistant",
                    instructions=instructions,
                    model=llm_model,
                    model_settings=ModelSettings(temperature=temperature, max_tokens=max_tokens),
                    mcp_servers=manager.active_servers,
                )
                result = Runner.run_streamed(agent, message, max_turns=max_turns)
                async for event in result.stream_events():
                    delta = _extract_delta(event)
                    if delta:
                        yield delta
        else:
            agent = Agent(
                name="Assistant",
                instructions=instructions,
                model=llm_model,
                model_settings=ModelSettings(temperature=temperature, max_tokens=max_tokens),
            )
            result = Runner.run_streamed(agent, message, max_turns=max_turns)
            async for event in result.stream_events():
                delta = _extract_delta(event)
                if delta:
                    yield delta

    try:
        for attempt in range(_MAX_500_RETRIES + 1):
            try:
                async for delta in _run_once():
                    yield delta
                return
            except Exception as e:
                err_msg = str(e).lower()
                if ("internal server error" in err_msg or "500" in err_msg) and attempt < _MAX_500_RETRIES:
                    delay = 3 * (attempt + 1)  # 3s, 6s
                    logger.warning(
                        "LM Studio 500, retrying in %ss (attempt %s/%s)",
                        delay,
                        attempt + 1,
                        _MAX_500_RETRIES + 1,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise
    except Exception as e:
        logger.exception("Agents SDK run failed")
        err_display = _format_exception_for_display(e)
        err_msg = err_display.lower()
        hint = ""
        if "connection" in err_msg and ("refused" in err_msg or "connect" in err_msg):
            hint = (
                " **LM Studio is not reachable.** Start LM Studio, load your model, and turn on "
                "the local server (Developer → Local Server). Ensure it's listening on port 1234."
            )
        elif "internal server error" in err_msg or "500" in err_msg:
            hint = (
                " LM Studio returned 500—check LM Studio logs for the underlying error. "
                "Can occur with multi-turn tool conversations or large requests."
            )
        elif "not in allowed" in err_msg or "permission" in err_msg or "path" in err_msg:
            hint = (
                " **Path not allowed.** Add the path to fast-filesystem's --allow list in "
                "Settings > Skills & MCP > fast-filesystem config."
            )
        yield f"**Agents SDK error:** {err_display}{hint}"

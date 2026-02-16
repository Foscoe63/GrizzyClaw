import logging
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Callable, Tuple

from grizzyclaw.config import Settings
from grizzyclaw.llm.router import LLMRouter
from grizzyclaw.memory.sqlite_store import SQLiteMemoryStore
from grizzyclaw.automation import CronScheduler, PLAYWRIGHT_AVAILABLE
from grizzyclaw.mcp_client import call_mcp_tool, discover_tools
from grizzyclaw.utils.vision import build_vision_content
from grizzyclaw.safety.content_filter import ContentFilter
from pathlib import Path
import ast
import json
import re
import asyncio

logger = logging.getLogger(__name__)

MAX_AGENTIC_ITERATIONS = 5  # Max tool-use rounds to prevent infinite loops

# Markers for messages worth keeping when trimming context
_CONTEXT_PRIORITY_MARKERS = (
    "[Tool result",
    "TOOL_CALL",
    "BROWSER_ACTION",
    "SCHEDULE_TASK",
    "MEMORY_SAVE",
    "\u2692",  # 🔧
)


def _message_has_priority_content(msg: Dict[str, Any]) -> bool:
    """True if message contains tool calls, results, or other high-value context."""
    content = msg.get("content", "") or ""
    if isinstance(content, list):
        content = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return any(m in content for m in _CONTEXT_PRIORITY_MARKERS)


def _trim_session(
    session: List[Dict[str, Any]], max_messages: int
) -> List[Dict[str, Any]]:
    """
    Trim session to max_messages, prioritizing recent messages and those with
    tool calls/results. Keeps the most recent messages and up to ~25% slots
    for older high-value turns.
    """
    if len(session) <= max_messages:
        return session

    # Always keep the most recent messages
    recent_count = max(max_messages - 4, max_messages // 2)
    recent = session[-recent_count:]
    older = session[:-recent_count]

    # From older, keep messages with tool content (most recent first among them)
    priority_slots = max_messages - len(recent)
    if priority_slots <= 0:
        return recent

    priority_in_older = [m for m in older if _message_has_priority_content(m)]
    kept_priority = priority_in_older[-priority_slots:]  # Most recent priority msgs

    return kept_priority + recent


def _strip_json_comments(s: str) -> str:
    """Remove // and /* */ comments from a string so json.loads accepts LLM output that includes comments."""
    # Remove // single-line comments (but not inside strings - rough pass: only when preceded by , or { or [ or :)
    s = re.sub(r",?\s*//[^\n]*", "", s)
    # Remove /* */ block comments
    s = re.sub(r"/\*[\s\S]*?\*/", "", s)
    return s


def _extract_balanced_brace(s: str, start: int) -> Optional[tuple[int, int]]:
    """From index of '{', return (start, end) of matching '}' (handles nesting)."""
    if start < 0 or start >= len(s) or s[start] != "{":
        return None
    depth = 0
    i = start
    in_string = None  # '"' or "'" when inside string
    escape = False
    while i < len(s):
        c = s[i]
        if escape:
            escape = False
            i += 1
            continue
        if c == "\\" and in_string:
            escape = True
            i += 1
            continue
        if in_string:
            if c == in_string:
                in_string = None
            i += 1
            continue
        if c in ('"', "'"):
            in_string = c
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return (start, i + 1)
        i += 1
    return None


def _extract_balanced_brace_dumb(s: str, start: int) -> Optional[tuple[int, int]]:
    """From index of '{', return (start, end) of matching '}' by counting braces only.
    Use when string-aware extraction fails (e.g. LLM output has \\\" breaking string tracking).
    """
    if start < 0 or start >= len(s) or s[start] != "{":
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return (start, i + 1)
    return None


def _find_json_blocks(text: str, prefix: str) -> list[str]:
    """Find all PREFIX = [optional ```] { ... } with balanced braces."""
    # Allow optional code fence between = and {
    pattern = re.compile(
        re.escape(prefix) + r"\s*=\s*(?:```(?:json)?\s*)?\{",
        re.IGNORECASE,
    )
    blocks: list[str] = []
    for m in pattern.finditer(text):
        brace_start = m.end() - 1
        pair = _extract_balanced_brace(text, brace_start)
        if pair is None:
            pair = _extract_balanced_brace_dumb(text, brace_start)
        if pair:
            blocks.append(text[pair[0] : pair[1]])
    return blocks


def _find_schedule_task_fallback(text: str) -> list[str]:
    """Fallback: find SCHEDULE_TASK then = then { and extract balanced block."""
    return _find_json_blocks_fallback(text, "SCHEDULE_TASK")


def _find_json_blocks_fallback(text: str, prefix: str) -> list[str]:
    """Fallback: find PREFIX = then { within 400 chars and extract balanced block."""
    blocks: list[str] = []
    idx = 0
    pattern = re.compile(re.escape(prefix) + r"\s*=", re.IGNORECASE)
    while True:
        m = pattern.search(text[idx:])
        if not m:
            break
        start = idx + m.end()
        window = text[start : start + 400]
        brace_in_window = window.find("{")
        if brace_in_window == -1:
            idx = start + 1
            continue
        brace = start + brace_in_window
        pair = _extract_balanced_brace_dumb(text, brace)
        if pair:
            blocks.append(text[pair[0] : pair[1]])
        idx = start + 1
    return blocks


def _strip_code_fence(s: str) -> str:
    """Remove leading/trailing markdown code fence lines."""
    s = s.strip()
    if s.startswith("```"):
        first = s.find("\n")
        if first != -1:
            s = s[first + 1 :]
        else:
            s = s[3:].strip()
    if s.rstrip().endswith("```"):
        s = s[: s.rfind("```")].rstrip()
    return s.strip()


def _normalize_llm_json(s: str) -> str:
    """Fix common LLM JSON: backslashes before quotes, smart quotes, code fences."""
    s = _strip_code_fence(s)
    s = _strip_json_comments(s)
    # Smart/curly quotes -> straight
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    # Key start: after { or , optional space then backslash(s) then " -> "
    s = re.sub(r'([{,]\s*)\\+"', r'\1"', s)
    s = re.sub(r'\\+":', '":', s)
    s = re.sub(r'\\+",', '",', s)
    s = re.sub(r'{\\+"', '{"', s)
    s = re.sub(r'\\+"}', '"}', s)
    s = re.sub(r'\\+"\s*}', '" }', s)
    s = re.sub(r':\s*\\+"', ': "', s)
    # Trailing comma before } or ] (invalid in JSON but common in LLM output)
    s = re.sub(r',\s*}', '}', s)
    s = re.sub(r',\s*]', ']', s)
    return s


# Browser automation - create fresh instance per request to avoid event loop issues
async def get_browser_instance():
    """Create a fresh browser automation instance
    
    Note: We create a new instance for each request because each call comes from
    a different asyncio.run() in the GUI thread, which creates a new event loop.
    Reusing a browser instance across event loops causes hangs.
    """
    if not PLAYWRIGHT_AVAILABLE:
        return None
    from grizzyclaw.automation.browser import BrowserAutomation
    return BrowserAutomation(headless=True)


def _scheduled_tasks_path() -> Path:
    """Path to persisted scheduled tasks (survives agent recreation)."""
    return Path.home() / ".grizzyclaw" / "scheduled_tasks.json"


class AgentCore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.llm_router = LLMRouter()
        self.llm_router.configure_from_settings(settings)
        self.memory = SQLiteMemoryStore(
            settings.database_url.replace("sqlite:///", ""),
            openai_api_key=settings.openai_api_key,
            use_semantic=True,
        )
        self.sessions: Dict[str, List[Dict[str, str]]] = {}
        self.scheduler = CronScheduler()
        self.scheduled_tasks_db: Dict[str, Dict] = {}  # Store task metadata
        self._load_scheduled_tasks()

    async def process_message(
        self,
        user_id: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        images: Optional[List[str]] = None,
    ) -> AsyncIterator[str]:
        # Get or create session
        if user_id not in self.sessions:
            self.sessions[user_id] = []

        session = self.sessions[user_id]

        # Retrieve relevant memories
        memories = await self.memory.retrieve(user_id, message, limit=5)
        memory_context = ""
        if memories:
            memory_context = "\n\nRelevant context from previous conversations:\n"
            for mem in memories:
                memory_context += f"- {mem.content}\n"

        # Build system prompt
        system_content = self.settings.system_prompt + """

## VISION

You can receive and analyze images. When the user attaches images, describe what you see or answer questions about them.

## MEMORY CAPABILITIES

You have PERSISTENT MEMORY. You can explicitly save important information the user wants you to remember.

To save something to memory, use this exact format anywhere in your response:
MEMORY_SAVE = { "content": "the information to remember", "category": "category_name" }

Categories: preferences, facts, tasks, notes, reminders, general

Examples:
- User says "Remember my favorite color is blue" -> MEMORY_SAVE = { "content": "User's favorite color is blue", "category": "preferences" }
- User says "My birthday is March 15" -> MEMORY_SAVE = { "content": "User's birthday is March 15", "category": "facts" }
- User says "Save this: meeting at 3pm" -> MEMORY_SAVE = { "content": "Meeting scheduled at 3pm", "category": "reminders" }

When users ask you to remember/save something, ALWAYS use MEMORY_SAVE. You CAN save to persistent memory.
After saving, confirm what you saved.

You also have access to memories from previous conversations shown below (if any).

## BROWSER AUTOMATION

You can control a web browser to browse pages, take screenshots, extract content, fill forms, and click elements.

Use this format:
BROWSER_ACTION = { "action": "action_name", "params": { ... } }

Available actions:
- navigate: { "url": "https://example.com" } - Go to a URL
- screenshot: { "full_page": true/false } - Take screenshot
- get_text: { "selector": "body" } - Get text from element (default: body)
- get_links: {} - Get all links on page
- click: { "selector": "button.submit" } - Click an element
- fill: { "selector": "input#email", "value": "text" } - Fill form field
- scroll: { "direction": "down", "amount": 500 } - Scroll page

Examples:
- "Go to google.com" -> BROWSER_ACTION = { "action": "navigate", "params": { "url": "https://google.com" } }
- "Take a screenshot" -> BROWSER_ACTION = { "action": "screenshot", "params": { "full_page": false } }
- "What's on this page?" -> BROWSER_ACTION = { "action": "get_text", "params": { "selector": "body" } }

## SCHEDULED TASKS

You can schedule tasks to run automatically at specific times using cron expressions.

Use this format:
SCHEDULE_TASK = { "action": "create/list/delete", "task": { ... } }

To create a task:
SCHEDULE_TASK = { "action": "create", "task": { "name": "Task Name", "cron": "0 9 * * *", "message": "What to do" } }

Cron format: minute hour day month weekday
- "0 9 * * *" = Every day at 9 AM
- "*/30 * * * *" = Every 30 minutes
- "0 0 * * 1" = Every Monday at midnight
- "0 */2 * * *" = Every 2 hours

To list tasks:
SCHEDULE_TASK = { "action": "list" }

To delete a task:
SCHEDULE_TASK = { "action": "delete", "task_id": "task-id-here" }

Examples:
- "Remind me to check email every morning at 9" -> SCHEDULE_TASK = { "action": "create", "task": { "name": "Check Email Reminder", "cron": "0 9 * * *", "message": "Time to check your email!" } }
- "What tasks do I have scheduled?" -> SCHEDULE_TASK = { "action": "list" }
"""
        if self.settings.rules_file:
            try:
                import yaml
                rules_path = Path(self.settings.rules_file).expanduser()
                if rules_path.exists():
                    with open(rules_path, 'r') as f:
                        rules_data = yaml.safe_load(f) or {}
                    if rules_data:
                        rules_str = yaml.dump(rules_data, default_flow_style=False)
                        system_content += f"\n\nFOLLOW THESE RULES:\n{rules_str}"
                else:
                    logger.warning(f"Rules file not found: {rules_path}")
            except Exception as e:
                logger.warning(f"Failed to load rules file: {e}")

        # MCP & skills: always add when we have servers or skills (not tied to rules_file)
        skills_str = ", ".join(self.settings.enabled_skills) if self.settings.enabled_skills else "none"
        mcp_file = Path(self.settings.mcp_servers_file).expanduser()
        mcp_list = []
        discovered_tools_map: Dict[str, List[Tuple[str, str]]] = {}
        if mcp_file.exists():
            try:
                with open(mcp_file, "r") as f:
                    data = json.load(f)
                mcp_servers_obj = data.get("mcpServers", {})
                for name, server_data in mcp_servers_obj.items():
                    if "url" in server_data:
                        url = server_data.get("url", "")[:80] + "..." if len(server_data.get("url", "")) > 80 else server_data.get("url", "")
                        mcp_list.append(f"- {name}: remote {url}")
                    else:
                        cmd = server_data.get("command", "")
                        args = server_data.get("args", [])
                        arg_str = (" ".join(str(a) for a in args[:6]) + "..." if len(args) > 6 else " ".join(str(a) for a in args)) if args else ""
                        mcp_list.append(f"- {name}: {cmd} {arg_str}".strip())
                # Dynamic tool discovery: connect to each server and list tools
                try:
                    discovered_tools_map = await discover_tools(mcp_file)
                except Exception as e:
                    logger.debug(f"Tool discovery failed: {e}")
            except Exception as e:
                logger.warning(f"Failed to load MCP file {mcp_file}: {e}")
        mcp_str = "\n".join(mcp_list) if mcp_list else "none"
        if mcp_list or skills_str != "none":
            # Build tool examples from discovered tools (so LLM knows exact names)
            tool_examples: List[str] = []
            for server_name, tools in discovered_tools_map.items():
                for tool_name, desc in tools[:5]:  # Limit per server to avoid huge prompts
                    short_desc = (desc[:60] + "...") if len(desc) > 60 else desc
                    tool_examples.append(f"- {server_name}: tool '{tool_name}' - {short_desc}")
            if tool_examples:
                examples_block = "\n".join(tool_examples[:20])  # Cap total examples
            else:
                examples_block = (
                    "- ddg-search: tool 'search' - web search\n"
                    "- fast-filesystem: tool 'fast_list_directory' - list directory\n"
                    "- context7: tool 'query-docs' - query documentation"
                )
            system_content += f"""

Enabled skills: {skills_str}

MCP servers:

{mcp_str}

## USING MCP & SKILLS

MCP servers provide tools. Use this exact format:

TOOL_CALL = {{ "mcp": "server_name", "tool": "tool_name", "arguments": {{ "param": "value" }} }}

Discovered tools (use these exact names):

{examples_block}

When users ask to search the web/internet, use ddg-search with tool 'search' if available. Agent executes tools and returns real results.
When using TOOL_CALL, write a brief intro first (e.g. 'Let me search for that.') so the user sees a natural response.
When you receive tool results in a follow-up message, use them to continue your response. Do NOT repeat the TOOL_CALL - the tools have already been executed."""

        if memories:
            system_content += f"\n\n{memory_context}"
        
        messages = [
            {"role": "system", "content": system_content}
        ]

        # Add session history
        messages.extend(session)

        # Add user message (with optional vision content)
        if images and any(images):
            text_for_session, content_blocks = build_vision_content(message or "What's in this image?", images)
            messages.append({"role": "user", "content": content_blocks})
            message = text_for_session  # For session storage and search triggers
        else:
            messages.append({"role": "user", "content": message})

        # Agentic loop: generate -> execute tools -> feed results back -> repeat
        mcp_file = Path(self.settings.mcp_servers_file).expanduser()
        accumulated_response = ""
        accumulated_tool_displays: List[str] = []  # For session storage
        current_messages = list(messages)
        search_triggers = ("search", "internet", "web", "look for", "find information", "look on", "search the")
        msg_lower = message.lower().strip()
        wants_search = any(t in msg_lower for t in search_triggers)

        try:
            for iteration in range(MAX_AGENTIC_ITERATIONS):
                response_chunks: List[str] = []
                content_filter = None
                if getattr(self.settings, "safety_content_filter", True):
                    policy = getattr(self.settings, "safety_policy", None)
                    custom = list(policy.get("custom_blocklist", [])) if isinstance(policy, dict) else []
                    content_filter = ContentFilter(custom_patterns=custom or None)

                async for chunk in self.llm_router.generate(
                    current_messages, temperature=0.7, max_tokens=2000
                ):
                    response_chunks.append(chunk)
                    out = chunk
                    if content_filter:
                        out, _ = content_filter.filter(out)
                    yield out

                response_text = "".join(response_chunks)
                accumulated_response += response_text

                # Parse MCP TOOL_CALLs
                tool_call_matches = _find_json_blocks(response_text, "TOOL_CALL")
                if not tool_call_matches:
                    tool_call_matches = _find_json_blocks_fallback(response_text, "TOOL_CALL")

                # Proactive search fallback: empty response + user wants search
                if wants_search and not tool_call_matches and len(response_text.strip()) < 50 and iteration == 0:
                    query = msg_lower
                    for phrase in ("look on the internet for", "search the internet for", "search for", "look for", "find information on", "find information about", "search the web for", "look up", "information on", "information about"):
                        if phrase in query:
                            query = query.split(phrase, 1)[-1].strip()
                            break
                    if not query or len(query) < 2:
                        query = message.strip()[:100]
                    if mcp_file.exists():
                        try:
                            tool_result = await call_mcp_tool(mcp_file, "ddg-search", "search", {"query": query})
                            result_display = f"Let me search for that.\n\n**🔧 ddg-search.search**\n{tool_result}\n"
                            if content_filter:
                                result_display, _ = content_filter.filter(result_display)
                            yield result_display
                            accumulated_tool_displays.append(result_display)
                            # Feed result back for next turn
                            current_messages.append({"role": "assistant", "content": response_text})
                            current_messages.append({
                                "role": "user",
                                "content": f"[Tool result ddg-search.search]\n{tool_result}\n\nUse this to continue your response."
                            })
                            continue
                        except Exception as e:
                            logger.warning(f"Proactive search fallback error: {e}")
                            err_display = f"I tried to search but encountered an error: {str(e)}. Make sure ddg-search MCP server is configured in Settings > Skills & MCP."
                            yield err_display
                            accumulated_tool_displays.append(err_display)

                if not tool_call_matches:
                    break  # No tools this turn - we're done

                # Execute tools and collect results
                tool_result_parts: List[str] = []
                for match_str in tool_call_matches:
                    try:
                        normalized = _normalize_llm_json(match_str)
                        tool_call = None
                        try:
                            tool_call = json.loads(normalized)
                        except json.JSONDecodeError:
                            try:
                                tool_call = ast.literal_eval(normalized)
                            except (ValueError, SyntaxError):
                                pass
                        if not tool_call or not isinstance(tool_call, dict):
                            continue
                        mcp_name = tool_call.get("mcp", "unknown")
                        tool_name = tool_call.get("tool", "unknown")
                        args = tool_call.get("arguments", {}) or {}

                        tool_result = await call_mcp_tool(mcp_file, mcp_name, tool_name, args)
                        result_display = f"\n\n**🔧 {mcp_name}.{tool_name}**\n{tool_result}\n"
                        if content_filter:
                            result_display, _ = content_filter.filter(result_display)
                        yield result_display
                        accumulated_tool_displays.append(result_display)
                        tool_result_parts.append(f"[Tool result {mcp_name}.{tool_name}]\n{tool_result}")
                    except Exception as e:
                        logger.warning(f"TOOL_CALL error: {e}")
                        err_msg = f"**❌ Tool error: {str(e)}**\n\n"
                        yield err_msg
                        accumulated_tool_displays.append(err_msg)
                        tool_result_parts.append(f"[Tool error]\n{str(e)}")

                # Feed tool results back for next LLM turn
                tool_results_msg = "\n\n".join(tool_result_parts) + "\n\nUse the above results to continue. Do NOT repeat the TOOL_CALL."
                current_messages.append({"role": "assistant", "content": response_text})
                current_messages.append({"role": "user", "content": tool_results_msg})

            # Final response for session/memory (LLM output + tool results user saw)
            response_text = accumulated_response
            if accumulated_tool_displays:
                response_text += "\n" + "".join(accumulated_tool_displays)

            # Parse and execute MEMORY_SAVE commands (balanced braces + normalize)
            memory_save_matches = _find_json_blocks(response_text, "MEMORY_SAVE")
            if not memory_save_matches:
                memory_save_matches = _find_json_blocks_fallback(response_text, "MEMORY_SAVE")
            for match_str in memory_save_matches:
                try:
                    normalized = _normalize_llm_json(match_str)
                    mem_data = None
                    try:
                        mem_data = json.loads(normalized)
                    except json.JSONDecodeError:
                        try:
                            mem_data = ast.literal_eval(normalized)
                        except (ValueError, SyntaxError):
                            pass
                    if not mem_data or not isinstance(mem_data, dict):
                        continue
                    content = mem_data.get("content", "")
                    category = mem_data.get("category", "general")
                    if content:
                        await self.memory.add(
                            user_id=user_id,
                            content=content,
                            category=category,
                            source="explicit_save",
                        )
                        logger.info(f"Memory saved for user {user_id}: {content[:50]}...")
                except Exception as e:
                    logger.warning(f"Memory save error: {e}")

            # Parse and execute BROWSER_ACTION commands (balanced braces + normalize like SCHEDULE_TASK)
            browser_matches = _find_json_blocks(response_text, "BROWSER_ACTION")
            if not browser_matches:
                browser_matches = _find_json_blocks_fallback(response_text, "BROWSER_ACTION")
            for match_str in browser_matches:
                try:
                    normalized = _normalize_llm_json(match_str)
                    browser_cmd = None
                    try:
                        browser_cmd = json.loads(normalized)
                    except json.JSONDecodeError:
                        try:
                            browser_cmd = ast.literal_eval(normalized)
                        except (ValueError, SyntaxError):
                            pass
                    if not browser_cmd or not isinstance(browser_cmd, dict):
                        continue
                    action = browser_cmd.get("action", "")
                    params = browser_cmd.get("params", {})
                    result = await self._execute_browser_action(action, params)
                    yield f"\n\n**🌐 Browser: {action}**\n{result}\n"
                except Exception as e:
                    logger.warning(f"BROWSER_ACTION error: {e}. Raw: {match_str[:200]}")
                    yield f"**❌ Browser error: {str(e)}**\n\n"

            # Parse and execute SCHEDULE_TASK commands
            schedule_matches = _find_json_blocks(response_text, "SCHEDULE_TASK")
            if not schedule_matches:
                schedule_matches = _find_schedule_task_fallback(response_text)
            for match_str in schedule_matches:
                try:
                    normalized = _normalize_llm_json(match_str)
                    schedule_cmd = None
                    try:
                        schedule_cmd = json.loads(normalized)
                    except json.JSONDecodeError:
                        try:
                            schedule_cmd = ast.literal_eval(normalized)
                        except (ValueError, SyntaxError):
                            pass
                    if not schedule_cmd or not isinstance(schedule_cmd, dict) or "action" not in schedule_cmd:
                        if schedule_cmd is None:
                            logger.warning(f"SCHEDULE_TASK parse failed. Raw: {match_str[:300]}")
                            yield "**❌ Invalid SCHEDULE_TASK JSON format.**\n\n"
                        continue
                    result = await self._execute_schedule_action(user_id, schedule_cmd)
                    yield f"\n\n**⏰ Scheduler**\n{result}\n"
                except Exception as e:
                    logger.exception("Scheduler action error")
                    yield f"**❌ Scheduler error: {str(e)}**\n\n"

            await self.memory.add(
                user_id=user_id,
                content=f"User: {message}\nAssistant: {response_text}",
                source="conversation",
            )

            # Update session
            session.append({"role": "user", "content": message})
            session.append({"role": "assistant", "content": response_text})

            # Smart context management: trim with priority for tool-heavy messages
            max_messages = getattr(self.settings, "max_session_messages", 20)
            session = _trim_session(session, max_messages)
            self.sessions[user_id] = session

        except Exception as e:
            logger.exception("Error generating response")
            err_msg = str(e).strip() or "Unknown error"
            yield f"Sorry, I encountered an error. {err_msg}"

    async def clear_session(self, user_id: str):
        if user_id in self.sessions:
            del self.sessions[user_id]

    async def get_user_memory(self, user_id: str) -> Dict[str, Any]:
        return await self.memory.get_user_memory(user_id)

    def get_user_memory_sync(self, user_id: str) -> Dict[str, Any]:
        """Synchronous wrapper for GUI"""
        return asyncio.run(self.get_user_memory(user_id))

    def list_memories_sync(self, user_id: str, limit: int = 50) -> list[dict]:
        """Synchronous list of memories as dicts for GUI"""
        memories = asyncio.run(self.memory.retrieve(user_id, "", limit))
        return [vars(mem) for mem in memories]

    def delete_memory_sync(self, item_id: str) -> bool:
        """Synchronous delete"""
        return asyncio.run(self.memory.delete(item_id))

    async def _execute_browser_action(self, action: str, params: Dict[str, Any]) -> str:
        """Execute a browser automation action"""
        if not PLAYWRIGHT_AVAILABLE:
            return "❌ Browser automation not available. Install with: `pip install playwright && playwright install chromium`"
        
        browser = None
        try:
            browser = await get_browser_instance()
            if browser is None:
                return "❌ Failed to initialize browser"
            
            if action == "navigate":
                url = params.get("url", "")
                if not url:
                    return "❌ URL required for navigate action"
                result = await browser.navigate(url)
                if result.success:
                    return f"✅ Navigated to: **{result.title}**\nURL: {result.url}"
                return f"❌ Navigation failed: {result.error}"
            
            elif action == "screenshot":
                full_page = params.get("full_page", False)
                result = await browser.screenshot(full_page=full_page)
                if result.success:
                    return f"✅ Screenshot saved: `{result.screenshot_path}`\nPage: {result.title}"
                return f"❌ Screenshot failed: {result.error}"
            
            elif action == "get_text":
                selector = params.get("selector", "body")
                result = await browser.get_text(selector)
                if result.success:
                    text = result.content[:2000] + "..." if len(result.content or "") > 2000 else result.content
                    return f"✅ Page content:\n```\n{text}\n```"
                return f"❌ Get text failed: {result.error}"
            
            elif action == "get_links":
                result = await browser.get_links()
                if result.success:
                    return f"✅ Links found:\n```json\n{result.content[:3000]}\n```"
                return f"❌ Get links failed: {result.error}"
            
            elif action == "click":
                selector = params.get("selector", "")
                if not selector:
                    return "❌ Selector required for click action"
                result = await browser.click(selector)
                if result.success:
                    return f"✅ Clicked element. Now on: **{result.title}**"
                return f"❌ Click failed: {result.error}"
            
            elif action == "fill":
                selector = params.get("selector", "")
                value = params.get("value", "")
                if not selector:
                    return "❌ Selector required for fill action"
                result = await browser.fill(selector, value)
                if result.success:
                    return f"✅ Filled input with value"
                return f"❌ Fill failed: {result.error}"
            
            elif action == "scroll":
                direction = params.get("direction", "down")
                amount = params.get("amount", 500)
                result = await browser.scroll(direction, amount)
                if result.success:
                    return f"✅ Scrolled {direction} by {amount}px"
                return f"❌ Scroll failed: {result.error}"
            
            elif action == "status":
                status = browser.get_status()
                return f"✅ Browser status:\n- Started: {status['started']}\n- URL: {status['current_url']}\n- Headless: {status['headless']}"
            
            else:
                return f"❌ Unknown browser action: {action}"
                
        except Exception as e:
            logger.error(f"Browser action error: {e}")
            return f"❌ Browser error: {str(e)}"
        finally:
            # Close browser to free resources and avoid event loop issues
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass

    async def _execute_schedule_action(self, user_id: str, schedule_cmd: Dict[str, Any]) -> str:
        """Execute a scheduler action"""
        action = schedule_cmd.get("action", "")
        
        if action == "create":
            task_data = schedule_cmd.get("task", {})
            name = task_data.get("name", "Unnamed Task")
            cron = task_data.get("cron", "")
            message = task_data.get("message", "")
            
            if not cron:
                return "❌ Cron expression required"
            if not message:
                return "❌ Task message required"
            
            task_id = f"task_{uuid.uuid4().hex[:8]}"
            
            # Create a handler that stores the message for later delivery
            async def task_handler():
                logger.info(f"Scheduled task fired: {name} - {message}")
                # Store in memory so user sees it
                await self.memory.add(
                    user_id=user_id,
                    content=f"⏰ SCHEDULED REMINDER: {message}",
                    category="reminders",
                    source="scheduler",
                )
            
            try:
                self.scheduler.schedule(task_id, name, cron, task_handler)
                self.scheduled_tasks_db[task_id] = {
                    "user_id": user_id,
                    "name": name,
                    "cron": cron,
                    "message": message
                }
                self._save_scheduled_tasks()

                # Start scheduler if not running
                if not self.scheduler.running:
                    asyncio.create_task(self.scheduler.start())

                next_run = self.scheduler.tasks[task_id].next_run
                next_run_str = next_run.strftime("%Y-%m-%d %H:%M") if next_run else "unknown"
                return f"✅ Task scheduled!\n- **ID:** `{task_id}`\n- **Name:** {name}\n- **Cron:** `{cron}`\n- **Next run:** {next_run_str}"
            except Exception as e:
                return f"❌ Failed to schedule task: {str(e)}"
        
        elif action == "list":
            stats = self.scheduler.get_stats()
            if not stats["tasks"]:
                return "📋 No scheduled tasks."
            
            lines = ["📋 **Scheduled Tasks:**\n"]
            for task in stats["tasks"]:
                status = "✅" if task["enabled"] else "❌"
                next_run = task["next_run"][:16] if task["next_run"] else "N/A"
                lines.append(f"- {status} **{task['name']}** (`{task['id']}`)")
                lines.append(f"  Cron: `{task['cron']}` | Next: {next_run} | Runs: {task['run_count']}")
            return "\n".join(lines)
        
        elif action == "delete":
            task_id = schedule_cmd.get("task_id", "")
            if not task_id:
                return "❌ task_id required for delete"
            
            if self.scheduler.unschedule(task_id):
                if task_id in self.scheduled_tasks_db:
                    del self.scheduled_tasks_db[task_id]
                self._save_scheduled_tasks()
                return f"✅ Task `{task_id}` deleted"
            return f"❌ Task `{task_id}` not found"
        
        elif action == "enable":
            task_id = schedule_cmd.get("task_id", "")
            self.scheduler.enable_task(task_id)
            return f"✅ Task `{task_id}` enabled"
        
        elif action == "disable":
            task_id = schedule_cmd.get("task_id", "")
            self.scheduler.disable_task(task_id)
            return f"✅ Task `{task_id}` disabled"
        
        else:
            return f"❌ Unknown scheduler action: {action}. Use: create, list, delete, enable, disable"

    def get_scheduled_tasks(self) -> List[Dict]:
        """Get list of scheduled tasks for GUI"""
        return self.scheduler.get_stats()["tasks"]

    def get_scheduler_status(self) -> Dict[str, Any]:
        """Get scheduler status"""
        return self.scheduler.get_stats()

    def reload_scheduled_tasks_from_disk(self) -> None:
        """Reload scheduled tasks from disk (call when opening Scheduler so list is current)."""
        self._load_scheduled_tasks()

    def _load_scheduled_tasks(self) -> None:
        """Load persisted tasks from disk so they show in Scheduler and survive agent recreation."""
        path = _scheduled_tasks_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("tasks", []):
                task_id = item.get("task_id") or item.get("id")
                name = item.get("name", "Unnamed")
                cron = item.get("cron", "")
                message = item.get("message", "")
                user_id = item.get("user_id", "gui_user")
                if not task_id or not cron or not message:
                    continue
                def make_handler(uid: str, msg: str):
                    async def h():
                        await self.memory.add(
                            user_id=uid,
                            content=f"⏰ SCHEDULED REMINDER: {msg}",
                            category="reminders",
                            source="scheduler",
                        )
                    return h
                handler = make_handler(user_id, message)
                self.scheduler.schedule(task_id, name, cron, handler)
                self.scheduled_tasks_db[task_id] = {
                    "user_id": user_id,
                    "name": name,
                    "cron": cron,
                    "message": message,
                }
            if self.scheduled_tasks_db:
                logger.info(f"Loaded {len(self.scheduled_tasks_db)} scheduled tasks from {path}")
        except Exception as e:
            logger.warning(f"Could not load scheduled tasks from {path}: {e}")

    def _save_scheduled_tasks(self) -> None:
        """Persist current tasks to disk."""
        path = _scheduled_tasks_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            tasks = [
                {
                    "task_id": tid,
                    "user_id": meta.get("user_id", "gui_user"),
                    "name": meta.get("name", ""),
                    "cron": meta.get("cron", ""),
                    "message": meta.get("message", ""),
                }
                for tid, meta in self.scheduled_tasks_db.items()
            ]
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"tasks": tasks}, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save scheduled tasks to {path}: {e}")

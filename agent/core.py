import ast
import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from grizzyclaw.automation import CronScheduler, PLAYWRIGHT_AVAILABLE
from grizzyclaw.config import Settings
from grizzyclaw.llm.router import LLMRouter
from grizzyclaw.mcp_client import call_mcp_tool, discover_tools
from grizzyclaw.memory.sqlite_store import SQLiteMemoryStore
from grizzyclaw.media.transcribe import transcribe_audio, TranscriptionError
from grizzyclaw.safety.content_filter import ContentFilter
from grizzyclaw.utils.vision import build_vision_content

from .command_parsers import (
    find_json_blocks,
    find_json_blocks_fallback,
    find_schedule_task_fallback,
    normalize_llm_json,
)
from .context_utils import trim_session
from grizzyclaw.workspaces.workspace import WorkspaceConfig
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grizzyclaw.workspaces.manager import WorkspaceManager
from .search_utils import (
    correct_search_query,
    simplify_search_query,
    simplify_search_query_retry,
)
import re
import time

logger = logging.getLogger(__name__)

MAX_AGENTIC_ITERATIONS = 5  # Max tool-use rounds to prevent infinite loops


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
        self.workspace_manager: Optional["WorkspaceManager"] = None
        self.workspace_id: str = ""
        self.workspace_config: Optional[WorkspaceConfig] = None
        self.scheduled_tasks_db: Dict[str, Dict] = {}  # Store task metadata
        self._file_watcher = None
        self._load_scheduled_tasks()
        if self.workspace_config and (
            self.workspace_config.proactive_habits
            or getattr(self.workspace_config, "proactive_screen", False)
            or getattr(self.workspace_config, "proactive_file_triggers", False)
        ):
            asyncio.create_task(self._init_proactive_tasks())

    async def process_message(
        self,
        user_id: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        images: Optional[List[str]] = None,
        audio_path: Optional[str] = None,
        audio_base64: Optional[str] = None,
    ) -> AsyncIterator[str]:
        # Evaluate automation triggers (fire webhooks, etc.)
        try:
            from grizzyclaw.automation.triggers import (
                execute_trigger_actions,
                get_matching_triggers,
            )

            ctx = {"message": message, "session_id": user_id, "user_id": user_id}
            matching = get_matching_triggers("message", ctx)
            if matching:
                await execute_trigger_actions(matching, ctx)
        except Exception as e:
            logger.warning("Trigger execution failed (message=%r): %s", message[:50], e)
        
        # Check for inter-agent @mentions (e.g. @coding analyze this code or @research find X)
        if self.workspace_manager and self.workspace_config and self.workspace_config.enable_inter_agent:
            # Match @target optional_colon message (until next \n@ or end)
            mentions = list(re.finditer(r"@([a-zA-Z0-9_]+)\s*:?\s*(.*?)(?=\n\s*@|\Z)", message, re.DOTALL))
            forwarded_any = False
            for match in mentions:
                target_name = match.group(1)
                forward_msg = match.group(2).strip()
                if forward_msg:
                    result = await self.workspace_manager.send_message_to_workspace(
                        self.workspace_id, target_name, forward_msg
                    )
                    preview = (forward_msg[:50] + "…") if len(forward_msg) > 50 else forward_msg
                    yield f"✅ Delegated to @{target_name}: {preview}\n"
                    if result and not result.startswith("Target ") and not result.startswith("Error:"):
                        yield f"@{target_name} replied: {result[:300]}{'…' if len(result) > 300 else ''}\n"
                    forwarded_any = True
            if forwarded_any:
                yield "Swarm delegations done.\n"
                return
        
        # Transcribe audio if provided
        if audio_path or audio_base64:
            loop = asyncio.get_event_loop()
            provider = getattr(
                self.settings, "transcription_provider", "openai"
            )
            if audio_path:
                source = audio_path
            else:
                source = f"data:audio/mpeg;base64,{audio_base64}"
            transcript = await loop.run_in_executor(
                None,
                lambda: transcribe_audio(
                    source,
                    provider=provider,
                    openai_api_key=self.settings.openai_api_key,
                ),
            )
            if transcript:
                message = f"{message}\n\n[Audio transcript]: {transcript}".strip() if message else f"[Audio transcript]: {transcript}"
            elif not message:
                # Save recording to Desktop for debugging when transcription fails
                debug_path = None
                if audio_path:
                    try:
                        src = Path(audio_path).expanduser()
                        if src.exists() and src.is_file():
                            desktop = Path.home() / "Desktop"
                            desktop.mkdir(exist_ok=True)
                            debug_path = desktop / "grizzyclaw_last_voice.wav"
                            import shutil
                            shutil.copy2(src, debug_path)
                    except Exception as e:
                        logger.debug(f"Could not save debug recording: {e}")

                if provider == "openai":
                    hint = "Add an OpenAI API key in Settings → Integrations."
                else:
                    hint = (
                        "Transcription returned no text. Speak clearly for 2–3+ seconds. "
                        "If input level is good, try: Settings → Sound → Input → select a different mic."
                    )
                if debug_path and debug_path.exists():
                    hint += f" Recording saved to Desktop as grizzyclaw_last_voice.wav — play it to verify the mic captured your voice."
                raise TranscriptionError(f"Transcription failed. {hint}")
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

## ABOUT GRIZZYCLAW

When users ask about GrizzyClaw's URLs or how to access it:
- Web Chat (when daemon runs): http://localhost:18788/chat
- Control UI: http://localhost:18788/control
- WebSocket Gateway: ws://127.0.0.1:18789
GrizzyClaw runs on HTTP by default (no built-in HTTPS). For HTTPS, use a reverse proxy or tunnel.

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
        
        # Build skill list for prompt
        skill_examples = ""
        if self.settings.enabled_skills:
            from grizzyclaw.skills.registry import get_skill
            for s_id in self.settings.enabled_skills:
                skill = get_skill(s_id)
                if skill:
                    skill_examples += f"- {skill.name}: {skill.description}\\n"
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
                # Dynamic tool discovery: connect to each server and list tools (timeout so chat is never blocked)
                try:
                    discovered_tools_map = await asyncio.wait_for(
                        discover_tools(mcp_file), timeout=5.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("MCP tool discovery timed out (5s); continuing without tool list")
                except Exception as e:
                    logger.info("MCP tool discovery failed: %s", e)
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

{skill_examples.strip() if skill_examples else ""}

## BUILT-IN SKILLS

Use SKILL_ACTION = {{\"skill\": \"skill_id\", \"action\": \"action_name\", \"params\": {{...}}}}

Examples:
- calendar: list_events {{}} or {{\"timeMin\": \"...\", \"maxResults\": 10}}, create_event {{\"summary\": \"Meeting\", \"start\": \"2026-02-20T10:00\", \"end\": \"11:00\", \"timezone\": \"UTC\"}}
- gmail: send_email {{\"to\": \"...\", \"subject\": \"...\", \"body\": \"...\"}}, reply {{\"thread_id\": \"...\", \"body\": \"...\"}}, list_messages {{\"q\": \"in:inbox\", \"maxResults\": 10}}
- github: list_prs {{\"repo\": \"owner/repo\", \"state\": \"open\"}}, list_issues {{\"repo\": \"owner/repo\"}}, create_issue {{\"repo\": \"owner/repo\", \"title\": \"Bug\", \"body\": \"...\"}}, get_pr {{\"repo\": \"owner/repo\", \"number\": 1}}
- mcp_marketplace: discover {{}} to list ClawHub MCP servers, install {{\"name\": \"playwright-mcp\"}} to add one

Configure API keys/tokens in Settings → Integrations first.

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
        start_time = time.perf_counter()
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
                tool_call_matches = find_json_blocks(response_text, "TOOL_CALL")
                if not tool_call_matches:
                    tool_call_matches = find_json_blocks_fallback(response_text, "TOOL_CALL")

                # Proactive search fallback: empty response + user wants search
                if wants_search and not tool_call_matches and len(response_text.strip()) < 50 and iteration == 0:
                    query = msg_lower
                    for phrase in (
                        "look on the internet for", "search the internet for",
                        "search the internet and see if you can get",
                        "search the internet and see if", "search the internet and",
                        "search for", "look for", "find information on",
                        "find information about", "search the web for",
                        "look up", "information on", "information about",
                    ):
                        if phrase in query:
                            query = query.split(phrase, 1)[-1].strip()
                            break
                    if not query or len(query) < 2:
                        query = message.strip()[:100]
                    query = correct_search_query(query)
                    query = simplify_search_query(query)
                    if mcp_file.exists():
                        try:
                            tool_result = await call_mcp_tool(mcp_file, "ddg-search", "search", {"query": query})
                            # Retry with shorter query if DuckDuckGo returned no results (bot detection / over-specific)
                            no_results = "no results" in (tool_result or "").lower() or "bot detection" in (tool_result or "").lower()
                            if no_results and len(query) > 25:
                                alt_query = simplify_search_query_retry(query)
                                if alt_query != query:
                                    tool_result = await call_mcp_tool(mcp_file, "ddg-search", "search", {"query": alt_query})
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
                        normalized = normalize_llm_json(match_str)
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
                        args = dict(tool_call.get("arguments", {}) or {})

                        # Correct and simplify search queries for better DuckDuckGo results
                        if mcp_name == "ddg-search" and tool_name == "search" and "query" in args:
                            q = correct_search_query(str(args["query"]))
                            args["query"] = simplify_search_query(q)

                        tool_result = await call_mcp_tool(mcp_file, mcp_name, tool_name, args)
                        # Retry with shorter query if DuckDuckGo returned no results
                        if (mcp_name == "ddg-search" and tool_name == "search" and "query" in args and
                            args["query"] and len(args["query"]) > 25):
                            no_results = "no results" in (tool_result or "").lower() or "bot detection" in (tool_result or "").lower()
                            if no_results:
                                alt = simplify_search_query_retry(args["query"])
                                if alt != args["query"]:
                                    tool_result = await call_mcp_tool(mcp_file, mcp_name, tool_name, {"query": alt})
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

            # Swarm: leader response may contain @mentions — run delegations and optionally consensus
            specialist_replies: List[Tuple[str, str]] = []
            if (
                self.workspace_manager
                and self.workspace_config
                and self.workspace_id
                and getattr(self.workspace_config, "swarm_role", "") == "leader"
                and getattr(self.workspace_config, "swarm_auto_delegate", False)
            ):
                leader_text = accumulated_response
                mentions = list(re.finditer(r"@([a-zA-Z0-9_]+)\s*:?\s*(.*?)(?=\n\s*@|\Z)", leader_text, re.DOTALL))
                for match in mentions:
                    target_name = match.group(1)
                    forward_msg = match.group(2).strip()
                    if not forward_msg:
                        continue
                    result = await self.workspace_manager.send_message_to_workspace(
                        self.workspace_id, target_name, forward_msg
                    )
                    if result and not result.startswith("Target ") and not result.startswith("Error:"):
                        specialist_replies.append((target_name, result))
                if specialist_replies:
                    yield "\n\n--- **Swarm delegations** ---\n"
                    for name, reply in specialist_replies:
                        yield f"\n**@{name}:** {reply[:400]}{'…' if len(reply) > 400 else ''}\n"
                    if getattr(self.workspace_config, "swarm_consensus", False) and specialist_replies:
                        synthesis_system = "You are the swarm leader. Synthesize the specialist responses below into one clear recommendation for the user. Be concise; combine the best points; do not simply repeat each response."
                        synthesis_user = f"User asked: {message}\n\nSpecialist responses:\n" + "\n\n".join(
                            f"[{name}]: {reply}" for name, reply in specialist_replies
                        )
                        messages_synthesis = [
                            {"role": "system", "content": synthesis_system},
                            {"role": "user", "content": synthesis_user},
                        ]
                        consensus_chunks: List[str] = []
                        async for chunk in self.llm_router.generate(
                            messages_synthesis, temperature=0.5, max_tokens=1500
                        ):
                            consensus_chunks.append(chunk)
                            yield chunk
                        consensus_text = "".join(consensus_chunks)
                        if consensus_text.strip():
                            response_text += "\n\n--- Swarm consensus ---\n" + consensus_text

            # Parse and execute MEMORY_SAVE commands (balanced braces + normalize)
            memory_save_matches = find_json_blocks(response_text, "MEMORY_SAVE")
            if not memory_save_matches:
                memory_save_matches = find_json_blocks_fallback(response_text, "MEMORY_SAVE")
            for match_str in memory_save_matches:
                try:
                    normalized = normalize_llm_json(match_str)
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
            browser_matches = find_json_blocks(response_text, "BROWSER_ACTION")
            if not browser_matches:
                browser_matches = find_json_blocks_fallback(response_text, "BROWSER_ACTION")
            for match_str in browser_matches:
                try:
                    normalized = normalize_llm_json(match_str)
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
            schedule_matches = find_json_blocks(response_text, "SCHEDULE_TASK")
            if not schedule_matches:
                schedule_matches = find_schedule_task_fallback(response_text)
            for match_str in schedule_matches:
                try:
                    normalized = normalize_llm_json(match_str)
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

            # Parse SKILL_ACTION (calendar, gmail, github, mcp_marketplace)
            skill_matches = find_json_blocks(response_text, "SKILL_ACTION")
            if not skill_matches:
                skill_matches = find_json_blocks_fallback(response_text, "SKILL_ACTION")
            for match_str in skill_matches:
                try:
                    normalized = normalize_llm_json(match_str)
                    skill_cmd = None
                    try:
                        skill_cmd = json.loads(normalized)
                    except json.JSONDecodeError:
                        try:
                            skill_cmd = ast.literal_eval(normalized)
                        except (ValueError, SyntaxError):
                            pass
                    if not skill_cmd or not isinstance(skill_cmd, dict):
                        continue
                    result = await self._execute_skill_action(skill_cmd)
                    yield f"\n\n**🛠️ Skill**\n{result}\n"
                except Exception as e:
                    logger.exception("Skill action error")
                    yield f"**❌ Skill error: {str(e)}**\n\n"

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
            session = trim_session(session, max_messages)
            self.sessions[user_id] = session

            # Update metrics
            delta_ms = (time.perf_counter() - start_time) * 1000
            input_chars = sum(len(str(msg.get('content', ''))) for msg in messages)
            output_chars = len(accumulated_response)
            est_input_tokens = input_chars // 4
            est_output_tokens = output_chars // 4
            if self.workspace_manager and self.workspace_id:
                ws = self.workspace_manager.get_workspace(self.workspace_id)
                if ws:
                    ws.message_count += 1
                    ws.total_response_time_ms += delta_ms
                    ws.total_input_tokens += est_input_tokens
                    ws.total_output_tokens += est_output_tokens
                    self.workspace_manager.update_workspace(
                        self.workspace_id,
                        message_count=ws.message_count,
                        total_response_time_ms=ws.total_response_time_ms,
                        total_input_tokens=ws.total_input_tokens,
                        total_output_tokens=ws.total_output_tokens,
                    )

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
        from grizzyclaw.utils.async_runner import run_async
        return run_async(self.get_user_memory(user_id))

    def list_memories_sync(self, user_id: str, limit: int = 50) -> list[dict]:
        """Synchronous list of memories as dicts for GUI"""
        from grizzyclaw.utils.async_runner import run_async
        memories = run_async(self.memory.retrieve(user_id, "", limit))
        return [vars(mem) for mem in memories]

    def delete_memory_sync(self, item_id: str) -> bool:
        """Synchronous delete"""
        from grizzyclaw.utils.async_runner import run_async
        return run_async(self.memory.delete(item_id))

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

    async def _execute_skill_action(self, skill_cmd: Dict[str, Any]) -> str:
        """Execute built-in skill: calendar, gmail, github, mcp_marketplace."""
        skill_id = (skill_cmd.get("skill") or skill_cmd.get("skill_id") or "").strip().lower()
        action = (skill_cmd.get("action") or "").strip().lower()
        params = skill_cmd.get("params") or skill_cmd
        if isinstance(params, dict):
            params = {k: v for k, v in params.items() if k not in ("skill", "skill_id", "action")}
        else:
            params = {}
        loop = asyncio.get_event_loop()
        try:
            from grizzyclaw.skills.executors import (
                execute_calendar,
                execute_gmail,
                execute_github,
                execute_mcp_marketplace,
            )
            if skill_id == "calendar":
                return await loop.run_in_executor(
                    None, lambda: execute_calendar(action, params, self.settings)
                )
            if skill_id == "gmail":
                return await loop.run_in_executor(
                    None, lambda: execute_gmail(action, params, self.settings)
                )
            if skill_id == "github":
                return await loop.run_in_executor(
                    None, lambda: execute_github(action, params, self.settings)
                )
            if skill_id == "mcp_marketplace":
                return await loop.run_in_executor(
                    None, lambda: execute_mcp_marketplace(action, params, self.settings)
                )
            return f"❌ Unknown skill: {skill_id}. Use calendar, gmail, github, or mcp_marketplace."
        except Exception as e:
            logger.exception("Skill execution error")
            return f"❌ Skill error: {e}"

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

    async def _init_proactive_tasks(self):
        """Initialize proactive scheduled tasks."""
        if "habit_daily" not in self.scheduler.tasks:
            self.scheduler.schedule(
                "habit_daily",
                "Daily Habit Analyzer",
                "0 9 * * *",  # Daily at 9am
                self._habit_analyzer
            )
            logger.info("Scheduled daily habit analyzer")
        if self.workspace_config.proactive_screen:
            if "screen_analyze" not in self.scheduler.tasks:
                self.scheduler.schedule(
                    "screen_analyze",
                    "Screen Context Analyzer (every 30min)",
                    "*/30 * * * *",
                    self._screen_analyzer
                )
                logger.info("Scheduled screen analyzer")
        if getattr(self.workspace_config, "proactive_file_triggers", False):
            try:
                from grizzyclaw.automation.file_watcher import FileWatcher
                from grizzyclaw.automation.triggers import get_matching_triggers, execute_trigger_actions
                loop = asyncio.get_running_loop()

                async def _on_file_or_git(ctx: dict) -> None:
                    event = ctx.get("event", "file_change")
                    rules = get_matching_triggers(event, ctx)
                    if not rules:
                        return
                    async def _inject(msg: str) -> None:
                        try:
                            async for _ in self.process_message("file_trigger", msg):
                                pass
                        except Exception as e:
                            logger.debug("Trigger agent message: %s", e)
                    await execute_trigger_actions(rules, ctx, agent_callback=_inject)

                self._file_watcher = FileWatcher(loop, _on_file_or_git)
                if self._file_watcher.start():
                    logger.info("File/Git watcher started for triggers")
            except Exception as e:
                logger.warning("Could not start file watcher: %s", e)

    async def _habit_analyzer(self):
        """Analyze memory patterns (memuBot-style) and auto-schedule habit-based actions."""
        logger.info("Running habit analyzer...")
        user_id = "proactive_user"
        # 1) Fallback: coding-related memories → prep env
        coding_memories = await self.memory.retrieve(user_id, "code OR git OR python OR program", limit=30)
        if len(coding_memories) >= 8 and "prep_coding" not in self.scheduler.tasks:
            self.scheduler.schedule(
                "prep_coding",
                "Prep Coding Environment (Mon-Fri)",
                "0 8 * * 1-5",
                self._prep_coding_handler,
            )
            logger.info("Detected coding habit, scheduled prep task")
        # 2) LLM-based habit learning: recent memories → suggest schedules
        try:
            recent = await self.memory.retrieve(user_id, "", limit=50)
            if len(recent) < 5:
                return
            lines = []
            for m in recent[:40]:
                ts = m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else "?"
                lines.append(f"- [{ts}] [{m.category or 'general'}] {m.content[:200]}")
            summary = "\n".join(lines)
            prompt = f"""Based on these recent memory entries, identify at most 3 recurring habits (e.g. "User codes weekdays", "User checks email mornings"). For each habit, suggest one scheduled action.
Output only a JSON array. Each item: {{"habit": "short description", "cron": "0 H * * D" (cron: minute hour day month weekday), "message": "reminder or action text"}}
Examples: "0 8 * * 1-5" = 8am Mon-Fri, "0 9 * * *" = 9am daily. No other text.

Memories:
{summary}"""
            messages = [
                {"role": "system", "content": "You output only valid JSON arrays. No markdown, no explanation."},
                {"role": "user", "content": prompt},
            ]
            out_chunks = []
            async for ch in self.llm_router.generate(messages, temperature=0.2, max_tokens=500):
                out_chunks.append(ch)
            raw = "".join(out_chunks).strip()
            # Strip markdown code block if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()
            suggestions = json.loads(raw)
            if not isinstance(suggestions, list):
                return
            for i, s in enumerate(suggestions[:3]):
                if not isinstance(s, dict) or "cron" not in s or "message" not in s:
                    continue
                habit = s.get("habit", "")[:80]
                cron = str(s.get("cron", ""))[:32]
                message = str(s.get("message", ""))[:200]
                task_id = f"habit_learned_{hash(habit + cron) % 10**8}"
                if task_id in self.scheduler.tasks:
                    continue
                try:
                    def _make_handler(msg: str):
                        async def _run():
                            await self._habit_learned_handler(msg)
                        return _run
                    self.scheduler.schedule(
                        task_id,
                        habit or "Habit-based reminder",
                        cron,
                        _make_handler(message),
                    )
                    logger.info("Habit learning: scheduled %s at %s", habit, cron)
                except Exception as e:
                    logger.debug("Habit schedule skip: %s", e)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("Habit learning parse skipped: %s", e)
        except Exception as e:
            logger.warning("Habit learning failed: %s", e)

    async def _prep_coding_handler(self):
        """Handler for coding prep action."""
        logger.info("🛠️ Prepping coding environment...")
        await self.memory.add("proactive_user", "Prepped coding env: opened projects dir.", category="tasks")

    async def _habit_learned_handler(self, message: str):
        """Handler for LLM-suggested habit reminders."""
        logger.info("📋 Habit reminder: %s", message)
        await self.memory.add("proactive_user", f"Habit reminder: {message}", category="reminders")

    async def _screen_analyzer(self):
        """Screen awareness: VL model on screenshot for desktop context (memuBot-style). Stores summary in memory when enabled."""
        import os
        import subprocess
        import tempfile

        logger.info("Running screen awareness analysis...")
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                temp_path = f.name
            subprocess.run(["screencapture", "-x", temp_path], check=True, capture_output=True, timeout=5)
            message = (
                "Analyze this screenshot of the user's desktop. Describe: which apps/windows are open, "
                "what the user is likely working on, and 1–2 proactive suggestions (e.g. reminder to save, "
                "suggest a break, or offer to help with the visible task). Be brief."
            )
            chunks = []
            async for chunk in self.process_message("screen_analyzer", message, images=[temp_path]):
                chunks.append(chunk)
                logger.info("%s", chunk.strip() or "")
            summary = "".join(chunks).strip()
            if summary and getattr(self.workspace_config, "proactive_screen", False):
                await self.memory.add(
                    "proactive_user",
                    f"Screen context: {summary[:500]}",
                    category="notes",
                    source="screen_analyzer",
                )
        except FileNotFoundError:
            logger.debug("screencapture not found (non-macOS or no GUI)")
        except subprocess.TimeoutExpired:
            logger.warning("Screen capture timed out")
        except Exception as e:
            logger.warning("Screen analysis failed: %s", e)
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
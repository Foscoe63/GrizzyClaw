"""Context management utilities for session trimming."""

from typing import Any, Dict, List

CONTEXT_PRIORITY_MARKERS = (
    "[Tool result",
    "TOOL_CALL",
    "BROWSER_ACTION",
    "SCHEDULE_TASK",
    "MEMORY_SAVE",
    "EXEC_COMMAND",
    "\u2692",  # 🔧
)


def message_has_priority_content(msg: Dict[str, Any]) -> bool:
    """True if message contains tool calls, results, or other high-value context."""
    content = msg.get("content", "") or ""
    if isinstance(content, list):
        content = " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return any(m in content for m in CONTEXT_PRIORITY_MARKERS)


def trim_session(
    session: List[Dict[str, Any]], max_messages: int
) -> List[Dict[str, Any]]:
    """
    Trim session to max_messages, prioritizing recent messages and those with
    tool calls/results. Keeps the most recent messages and up to ~25% slots
    for older high-value turns.
    """
    if len(session) <= max_messages:
        return session

    recent_count = max(max_messages - 4, max_messages // 2)
    recent = session[-recent_count:]
    older = session[:-recent_count]

    priority_slots = max_messages - len(recent)
    if priority_slots <= 0:
        return recent

    priority_in_older = [m for m in older if message_has_priority_content(m)]
    kept_priority = priority_in_older[-priority_slots:]

    return kept_priority + recent

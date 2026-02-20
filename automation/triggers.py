"""Advanced automation triggers - conditional event-driven actions"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

import json
import re

logger = logging.getLogger(__name__)

# Supported events: "message", "webhook", "schedule", "file_change", "git_event"
FILE_CHANGE_EVENT = "file_change"
GIT_EVENT = "git_event"


@dataclass
class TriggerCondition:
    """Condition for when a trigger fires."""
    type: str  # "contains", "matches", "equals", "cron", "path_matches" (for file_change/git_event)
    value: Any  # pattern, regex, or cron expr


@dataclass
class TriggerAction:
    """Action to perform when trigger fires."""
    type: str  # "agent_message", "webhook", "notify"
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TriggerRule:
    """A single automation trigger rule."""
    id: str
    name: str
    enabled: bool = True
    event: str = "message"  # message, webhook, schedule
    condition: Optional[TriggerCondition] = None
    action: TriggerAction = field(default_factory=lambda: TriggerAction("agent_message", {}))
    description: str = ""


def load_triggers(config_path: Optional[Path] = None) -> List[TriggerRule]:
    """Load trigger rules from JSON config."""
    config_path = config_path or Path.home() / ".grizzyclaw" / "triggers.json"
    if not config_path.exists():
        return []
    try:
        with open(config_path, "r") as f:
            data = json.load(f)
        rules = []
        for r in data.get("triggers", []):
            cond = None
            if r.get("condition"):
                c = r["condition"]
                cond = TriggerCondition(type=c.get("type", "contains"), value=c.get("value", ""))
            action = TriggerAction(
                type=r.get("action", {}).get("type", "agent_message"),
                config=r.get("action", {}).get("config", {}),
            )
            rules.append(TriggerRule(
                id=r.get("id", ""),
                name=r.get("name", "Unnamed"),
                enabled=r.get("enabled", True),
                event=r.get("event", "message"),
                condition=cond,
                action=action,
                description=r.get("description", ""),
            ))
        return rules
    except Exception as e:
        logger.warning(f"Could not load triggers: {e}")
        return []


def save_triggers(rules: List[TriggerRule], config_path: Optional[Path] = None) -> bool:
    """Save trigger rules to JSON config."""
    config_path = config_path or Path.home() / ".grizzyclaw" / "triggers.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = {"triggers": []}
        for r in rules:
            entry = {
                "id": r.id,
                "name": r.name,
                "enabled": r.enabled,
                "event": r.event,
                "description": r.description,
                "action": {"type": r.action.type, "config": r.action.config},
            }
            if r.condition:
                entry["condition"] = {"type": r.condition.type, "value": r.condition.value}
            data["triggers"].append(entry)
        with open(config_path, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Could not save triggers: {e}")
        return False


def evaluate_condition(condition: TriggerCondition, context: Dict[str, Any]) -> bool:
    """Check if condition matches context."""
    if not condition:
        return True
    value = condition.value
    if condition.type == "path_matches":
        path = str(context.get("path", context.get("file_path", "")))
        try:
            return bool(re.search(value, path))
        except re.error:
            return False
    if condition.type == "contains":
        text = str(context.get("message", context.get("text", "")))
        return value.lower() in text.lower()
    if condition.type == "matches":
        text = str(context.get("message", context.get("text", "")))
        try:
            return bool(re.search(value, text, re.IGNORECASE))
        except re.error:
            return False
    if condition.type == "equals":
        text = str(context.get("message", context.get("text", "")))
        return text.strip().lower() == str(value).strip().lower()
    return False


def get_matching_triggers(
    event: str,
    context: Dict[str, Any],
    rules: Optional[List[TriggerRule]] = None,
) -> List[TriggerRule]:
    """Get triggers that match the event and context."""
    rules = rules or load_triggers()
    matching = []
    for r in rules:
        if not r.enabled or r.event != event:
            continue
        if evaluate_condition(r.condition, context):
            matching.append(r)
    return matching


async def execute_trigger_actions(
    rules: List[TriggerRule],
    context: Dict[str, Any],
    agent_callback: Optional[Callable[[str], Awaitable[None]]] = None,
) -> None:
    """Execute actions for matching triggers (webhooks, notify, or send message to agent)."""
    import asyncio

    for rule in rules:
        if rule.action.type == "webhook":
            url = rule.action.config.get("url")
            if url:
                asyncio.create_task(_fire_webhook(url, context))
        elif rule.action.type == "notify":
            logger.info(
                "Trigger '%s' matched: %s",
                rule.name,
                (context.get("message") or context.get("path") or "")[:50],
            )
        elif rule.action.type == "agent_message" and agent_callback:
            template = rule.action.config.get("message_template") or "Event: {event}. Path: {path}."
            try:
                message = template.format(**context)
            except KeyError:
                message = template + " " + str(context)
            asyncio.create_task(_run_agent_callback(agent_callback, message))


async def _run_agent_callback(
    agent_callback: Callable[[str], Awaitable[None]], message: str
) -> None:
    """Run agent callback (e.g. inject message into agent)."""
    try:
        await agent_callback(message)
    except Exception as e:
        logger.warning("Agent trigger callback failed: %s", e)


async def _fire_webhook(url: str, context: Dict[str, Any]) -> None:
    """Fire webhook POST (non-blocking, errors logged)."""
    try:
        import aiohttp

        payload = {
            "message": context.get("message", ""),
            "session_id": context.get("session_id", "default"),
            "user_id": context.get("user_id", ""),
            "path": context.get("path", ""),
            "event": context.get("event", ""),
            "change_type": context.get("change_type", ""),
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status >= 400:
                    logger.warning(f"Webhook {url} returned {resp.status}")
    except Exception as e:
        logger.warning(f"Webhook {url} failed: {e}")

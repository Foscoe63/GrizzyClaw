"""Advanced automation triggers - conditional event-driven actions"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import json
import re

logger = logging.getLogger(__name__)


@dataclass
class TriggerCondition:
    """Condition for when a trigger fires."""
    type: str  # "contains", "matches", "equals", "cron"
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
    text = str(context.get("message", context.get("text", "")))
    value = condition.value
    if condition.type == "contains":
        return value.lower() in text.lower()
    if condition.type == "matches":
        try:
            return bool(re.search(value, text, re.IGNORECASE))
        except re.error:
            return False
    if condition.type == "equals":
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
) -> None:
    """Execute actions for matching triggers (e.g. fire webhooks). Non-blocking."""
    import asyncio

    for rule in rules:
        if rule.action.type == "webhook":
            url = rule.action.config.get("url")
            if url:
                asyncio.create_task(_fire_webhook(url, context))
        elif rule.action.type == "notify":
            logger.info(
                f"Trigger '{rule.name}' matched: {context.get('message', '')[:50]}..."
            )


async def _fire_webhook(url: str, context: Dict[str, Any]) -> None:
    """Fire webhook POST (non-blocking, errors logged)."""
    try:
        import aiohttp

        payload = {
            "message": context.get("message", ""),
            "session_id": context.get("session_id", "default"),
            "user_id": context.get("user_id", ""),
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status >= 400:
                    logger.warning(f"Webhook {url} returned {resp.status}")
    except Exception as e:
        logger.warning(f"Webhook {url} failed: {e}")

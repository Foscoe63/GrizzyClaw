"""Swarm-level event bus for agents: broadcast state and subscribe by channel."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SwarmEvent:
    """Event emitted by an agent in the swarm (e.g. task done, state change)."""
    type: str
    data: Dict[str, Any]
    workspace_id: Optional[str] = None
    channel: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


class SwarmEventBus:
    """
    Event bus for swarm agents: broadcast state changes and subscribe by event type
    and optionally by channel. Used for agent-to-agent awareness (e.g. "I finished
    compiling module X") without going through the leader.
    """
    def __init__(self):
        self._handlers: Dict[str, List[Callable[..., Any]]] = {}
        self._history: List[SwarmEvent] = []
        self._max_history = 500

    def on(
        self,
        event_type: str,
        handler: Callable[..., Any],
        channel: Optional[str] = None,
    ) -> None:
        """Subscribe to an event type. If channel is set, only events with that channel are passed."""
        key = (event_type, channel)
        if key not in self._handlers:
            self._handlers[key] = []
        self._handlers[key].append(handler)
        logger.debug("Swarm bus: subscribed to %s channel=%s", event_type, channel)

    def off(
        self,
        event_type: str,
        handler: Callable[..., Any],
        channel: Optional[str] = None,
    ) -> None:
        """Unsubscribe from an event type."""
        key = (event_type, channel)
        if key in self._handlers:
            try:
                self._handlers[key].remove(handler)
            except ValueError:
                pass

    async def emit(
        self,
        event_type: str,
        data: Dict[str, Any],
        workspace_id: Optional[str] = None,
        channel: Optional[str] = None,
    ) -> None:
        """Emit an event to subscribers. Subscribers for this type (and optionally this channel) are notified."""
        event = SwarmEvent(
            type=event_type,
            data=data,
            workspace_id=workspace_id,
            channel=channel,
        )
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history.pop(0)

        # Notify handlers that match (event_type, None) or (event_type, channel)
        for (key_type, key_channel), handlers in list(self._handlers.items()):
            if key_type != event_type:
                continue
            if key_channel is not None and key_channel != channel:
                continue
            for h in handlers:
                try:
                    if asyncio.iscoroutinefunction(h):
                        await h(event)
                    else:
                        h(event)
                except Exception as e:
                    logger.error("Swarm event handler error: %s", e, exc_info=True)

    def get_history(
        self,
        event_type: Optional[str] = None,
        channel: Optional[str] = None,
        limit: int = 100,
    ) -> List[SwarmEvent]:
        """Return recent events, optionally filtered by type and channel."""
        out = self._history
        if event_type:
            out = [e for e in out if e.type == event_type]
        if channel is not None:
            out = [e for e in out if e.channel == channel]
        return out[-limit:]


# Swarm event type constants for agents
class SwarmEventTypes:
    TASK_COMPLETED = "task_completed"
    STATE_CHANGED = "state_changed"
    SUBTASK_AVAILABLE = "subtask_available"  # For dynamic role allocation
    SUBTASK_CLAIMED = "subtask_claimed"      # Specialist claims a subtask (task_id, slug, workspace_id)
    DEBATE_REQUEST = "debate_request"
    DEBATE_RESPONSE = "debate_response"
    CONSENSUS_READY = "consensus_ready"
    REQUEST_TO_SPECIALIST = "request_to_specialist"  # One specialist asks another (target_slug, message, from_slug)

"""Event bus for gateway communication"""

import asyncio
import logging
from typing import Dict, List, Callable, Any, Optional
from dataclasses import dataclass, field
import time

logger = logging.getLogger(__name__)


@dataclass
class Event:
    """Event that flows through the system"""
    type: str
    data: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


class EventBus:
    """Central event bus for pub/sub communication

    Allows components to communicate without direct coupling
    """

    def __init__(self):
        self.handlers: Dict[str, List[Callable]] = {}
        self.event_history: List[Event] = []
        self.max_history = 1000

    def on(self, event_type: str, handler: Callable):
        """Subscribe to an event type

        Args:
            event_type: Type of event to listen for
            handler: Async function to call when event occurs
        """
        if event_type not in self.handlers:
            self.handlers[event_type] = []

        self.handlers[event_type].append(handler)
        logger.debug(f"Registered handler for event: {event_type}")

    def off(self, event_type: str, handler: Callable):
        """Unsubscribe from an event type

        Args:
            event_type: Event type
            handler: Handler to remove
        """
        if event_type in self.handlers:
            try:
                self.handlers[event_type].remove(handler)
                logger.debug(f"Unregistered handler for event: {event_type}")
            except ValueError:
                pass

    async def emit(self, event: Event):
        """Emit an event to all subscribers

        Args:
            event: Event to emit
        """
        # Add to history
        self.event_history.append(event)
        if len(self.event_history) > self.max_history:
            self.event_history.pop(0)

        # Call all handlers for this event type
        if event.type in self.handlers:
            handlers = self.handlers[event.type]
            logger.debug(f"Emitting event '{event.type}' to {len(handlers)} handlers")

            # Call all handlers
            for handler in handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(event)
                    else:
                        handler(event)
                except Exception as e:
                    logger.error(f"Error in event handler: {e}", exc_info=True)

    def get_history(self, event_type: Optional[str] = None, limit: int = 100) -> List[Event]:
        """Get event history

        Args:
            event_type: Filter by event type (optional)
            limit: Maximum number of events to return

        Returns:
            List of events
        """
        if event_type:
            events = [e for e in self.event_history if e.type == event_type]
        else:
            events = self.event_history

        return events[-limit:]

    def clear_history(self):
        """Clear event history"""
        self.event_history.clear()


# Common event types
class EventTypes:
    """Standard event types"""

    # Messages
    MESSAGE_RECEIVED = "message_received"
    MESSAGE_SENT = "message_sent"
    MESSAGE_ERROR = "message_error"

    # Sessions
    SESSION_CREATED = "session_created"
    SESSION_ENDED = "session_ended"
    SESSION_UPDATED = "session_updated"

    # Channels
    CHANNEL_CONNECTED = "channel_connected"
    CHANNEL_DISCONNECTED = "channel_disconnected"
    CHANNEL_ERROR = "channel_error"

    # Presence
    USER_ONLINE = "user_online"
    USER_OFFLINE = "user_offline"
    USER_TYPING = "user_typing"

    # System
    SYSTEM_STARTUP = "system_startup"
    SYSTEM_SHUTDOWN = "system_shutdown"
    SYSTEM_ERROR = "system_error"

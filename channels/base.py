"""Base channel abstraction for all messaging platforms"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class MessageType(Enum):
    """Message type enum"""
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    FILE = "file"
    LOCATION = "location"
    CONTACT = "contact"


class ChannelStatus(Enum):
    """Channel connection status"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class ChannelUser:
    """User information from a channel"""
    id: str
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def full_name(self) -> str:
        """Get full name"""
        if self.display_name:
            return self.display_name
        parts = []
        if self.first_name:
            parts.append(self.first_name)
        if self.last_name:
            parts.append(self.last_name)
        return " ".join(parts) if parts else self.username or self.id


@dataclass
class ChannelMessage:
    """Message from a channel"""
    message_id: str
    user: ChannelUser
    content: str
    message_type: MessageType = MessageType.TEXT
    timestamp: datetime = field(default_factory=datetime.now)
    reply_to: Optional[str] = None
    channel_id: Optional[str] = None
    chat_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "message_id": self.message_id,
            "user": {
                "id": self.user.id,
                "username": self.user.username,
                "full_name": self.user.full_name
            },
            "content": self.content,
            "type": self.message_type.value,
            "timestamp": self.timestamp.isoformat(),
            "reply_to": self.reply_to,
            "channel_id": self.channel_id,
            "chat_id": self.chat_id
        }


class Channel(ABC):
    """Base class for all messaging channels

    All messaging platforms (Telegram, WhatsApp, Slack, etc.) should inherit from this
    """

    def __init__(self, channel_name: str, config: Dict[str, Any]):
        """Initialize channel

        Args:
            channel_name: Name of the channel (e.g., 'telegram', 'whatsapp')
            config: Channel-specific configuration
        """
        self.channel_name = channel_name
        self.config = config
        self.status = ChannelStatus.DISCONNECTED
        self.event_handlers: Dict[str, List[callable]] = {}

    @abstractmethod
    async def start(self):
        """Start the channel connection

        Should connect to the messaging platform and begin receiving messages
        """
        pass

    @abstractmethod
    async def stop(self):
        """Stop the channel connection gracefully"""
        pass

    @abstractmethod
    async def send_message(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        **kwargs
    ) -> bool:
        """Send a message to a chat

        Args:
            chat_id: Chat/conversation identifier
            content: Message content
            reply_to: Message ID to reply to (optional)
            **kwargs: Platform-specific options

        Returns:
            True if sent successfully, False otherwise
        """
        pass

    @abstractmethod
    async def send_typing_indicator(self, chat_id: str):
        """Send typing indicator to show bot is processing

        Args:
            chat_id: Chat identifier
        """
        pass

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        **kwargs
    ) -> bool:
        """Send an image (optional, not all channels support)

        Args:
            chat_id: Chat identifier
            image_url: URL or path to image
            caption: Optional caption

        Returns:
            True if sent, False if not supported/failed
        """
        logger.warning(f"Channel {self.channel_name} does not support sending images")
        return False

    async def send_file(
        self,
        chat_id: str,
        file_url: str,
        filename: Optional[str] = None,
        **kwargs
    ) -> bool:
        """Send a file (optional, not all channels support)

        Args:
            chat_id: Chat identifier
            file_url: URL or path to file
            filename: Optional filename

        Returns:
            True if sent, False if not supported/failed
        """
        logger.warning(f"Channel {self.channel_name} does not support sending files")
        return False

    def on(self, event: str, handler: callable):
        """Register an event handler

        Args:
            event: Event name (e.g., 'message', 'error', 'connected')
            handler: Async function to handle the event
        """
        if event not in self.event_handlers:
            self.event_handlers[event] = []
        self.event_handlers[event].append(handler)
        logger.debug(f"Registered handler for event '{event}' on {self.channel_name}")

    async def emit(self, event: str, *args, **kwargs):
        """Emit an event to all registered handlers

        Args:
            event: Event name
            *args: Positional arguments for handlers
            **kwargs: Keyword arguments for handlers
        """
        if event in self.event_handlers:
            for handler in self.event_handlers[event]:
                try:
                    await handler(*args, **kwargs)
                except Exception as e:
                    logger.error(f"Error in event handler for '{event}': {e}", exc_info=True)

    def get_status(self) -> Dict[str, Any]:
        """Get channel status information

        Returns:
            Status dictionary
        """
        return {
            "channel": self.channel_name,
            "status": self.status.value,
            "config": {k: "***" if "key" in k.lower() or "token" in k.lower() else v
                      for k, v in self.config.items()}
        }

    async def health_check(self) -> bool:
        """Check if channel is healthy and connected

        Returns:
            True if healthy, False otherwise
        """
        return self.status == ChannelStatus.CONNECTED

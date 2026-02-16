"""Channel abstraction layer for multi-platform messaging"""

from .base import Channel, ChannelMessage, ChannelUser, MessageType, ChannelStatus
from .registry import ChannelRegistry
from .telegram import TelegramChannel

__all__ = [
    "Channel",
    "ChannelMessage",
    "ChannelUser",
    "MessageType",
    "ChannelStatus",
    "ChannelRegistry",
    "TelegramChannel",
]

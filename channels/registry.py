"""Channel registry for managing all messaging channels"""

import logging
from typing import Dict, Optional, List, Any
from .base import Channel, ChannelStatus

logger = logging.getLogger(__name__)


class ChannelRegistry:
    """Central registry for all messaging channels

    Manages lifecycle of channels (Telegram, WhatsApp, Slack, etc.)
    """

    def __init__(self):
        self.channels: Dict[str, Channel] = {}
        self.default_channel: Optional[str] = None

    def register(self, channel: Channel, set_default: bool = False):
        """Register a channel

        Args:
            channel: Channel instance
            set_default: Set as default channel if True
        """
        if channel.channel_name in self.channels:
            logger.warning(f"Channel {channel.channel_name} already registered, replacing")

        self.channels[channel.channel_name] = channel
        logger.info(f"Registered channel: {channel.channel_name}")

        if set_default or not self.default_channel:
            self.default_channel = channel.channel_name
            logger.info(f"Set default channel: {channel.channel_name}")

    def unregister(self, channel_name: str) -> bool:
        """Unregister a channel

        Args:
            channel_name: Name of channel to remove

        Returns:
            True if removed, False if not found
        """
        if channel_name in self.channels:
            del self.channels[channel_name]
            logger.info(f"Unregistered channel: {channel_name}")

            # Update default if necessary
            if self.default_channel == channel_name:
                self.default_channel = next(iter(self.channels.keys()), None)

            return True
        return False

    def get(self, channel_name: str) -> Optional[Channel]:
        """Get a channel by name

        Args:
            channel_name: Channel name

        Returns:
            Channel instance or None if not found
        """
        return self.channels.get(channel_name)

    def get_default(self) -> Optional[Channel]:
        """Get the default channel

        Returns:
            Default channel or None
        """
        if self.default_channel:
            return self.channels.get(self.default_channel)
        return None

    def list_channels(self) -> List[str]:
        """List all registered channel names

        Returns:
            List of channel names
        """
        return list(self.channels.keys())

    async def start_all(self):
        """Start all registered channels"""
        logger.info(f"Starting {len(self.channels)} channels...")

        for name, channel in self.channels.items():
            try:
                logger.info(f"Starting channel: {name}")
                await channel.start()
                logger.info(f"✓ Channel {name} started")
            except Exception as e:
                logger.error(f"✗ Failed to start channel {name}: {e}", exc_info=True)
                channel.status = ChannelStatus.ERROR

    async def stop_all(self):
        """Stop all registered channels"""
        logger.info(f"Stopping {len(self.channels)} channels...")

        for name, channel in self.channels.items():
            try:
                logger.info(f"Stopping channel: {name}")
                await channel.stop()
                logger.info(f"✓ Channel {name} stopped")
            except Exception as e:
                logger.error(f"✗ Failed to stop channel {name}: {e}", exc_info=True)

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics for all channels

        Returns:
            Statistics dictionary
        """
        total = len(self.channels)
        connected = sum(1 for c in self.channels.values()
                       if c.status == ChannelStatus.CONNECTED)
        error = sum(1 for c in self.channels.values()
                   if c.status == ChannelStatus.ERROR)

        return {
            "total_channels": total,
            "connected": connected,
            "error": error,
            "disconnected": total - connected - error,
            "default_channel": self.default_channel,
            "channels": {
                name: channel.get_status()
                for name, channel in self.channels.items()
            }
        }

    async def health_check(self) -> Dict[str, bool]:
        """Check health of all channels

        Returns:
            Dictionary mapping channel names to health status
        """
        results = {}
        for name, channel in self.channels.items():
            try:
                results[name] = await channel.health_check()
            except Exception as e:
                logger.error(f"Health check failed for {name}: {e}")
                results[name] = False

        return results

"""WhatsApp channel implementation

Note: This requires whatsapp-web.py or similar library
Install with: pip install whatsapp-web.py
"""

import logging
from typing import Optional
import asyncio

from grizzyclaw.config import Settings
from grizzyclaw.agent.core import AgentCore
from .base import Channel, ChannelMessage, ChannelUser, MessageType, ChannelStatus

logger = logging.getLogger(__name__)


class WhatsAppChannel(Channel):
    """WhatsApp messaging channel

    Uses QR code authentication for WhatsApp Web protocol
    """

    def __init__(self, settings: Settings):
        """Initialize WhatsApp channel

        Args:
            settings: Application settings
        """
        config = {
            "session_path": settings.whatsapp_session_path or "~/.grizzyclaw/whatsapp_session"
        }
        super().__init__("whatsapp", config)

        self.settings = settings
        self.client = None
        self.agent: Optional[AgentCore] = None
        self._ready = False

    async def start(self):
        """Start WhatsApp channel"""
        logger.info("Starting WhatsApp channel...")
        self.status = ChannelStatus.CONNECTING

        try:
            # Import here to make it optional
            try:
                from whatsapp import Client
            except ImportError:
                logger.error(
                    "whatsapp-web.py not installed. "
                    "Install with: pip install whatsapp-web.py"
                )
                self.status = ChannelStatus.ERROR
                return

            # Initialize agent
            self.agent = AgentCore(self.settings)

            # Initialize WhatsApp client
            self.client = Client(session_path=self.config["session_path"])

            # Register event handlers
            self.client.on("ready", self._on_ready)
            self.client.on("message", self._on_message)
            self.client.on("qr", self._on_qr)
            self.client.on("disconnected", self._on_disconnected)

            # Start client
            await self.client.start()

            logger.info("✓ WhatsApp channel started")
            logger.info("  Scan QR code if prompted")

        except Exception as e:
            logger.error(f"Failed to start WhatsApp channel: {e}", exc_info=True)
            self.status = ChannelStatus.ERROR
            await self.emit("error", error=str(e))
            raise

    async def stop(self):
        """Stop WhatsApp channel"""
        logger.info("Stopping WhatsApp channel...")

        if self.client:
            try:
                await self.client.logout()
            except:
                pass

        self.status = ChannelStatus.DISCONNECTED
        await self.emit("disconnected")
        logger.info("✓ WhatsApp channel stopped")

    async def send_message(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        **kwargs
    ) -> bool:
        """Send a message via WhatsApp

        Args:
            chat_id: WhatsApp chat ID (phone number with country code)
            content: Message text
            reply_to: Message ID to reply to
            **kwargs: Additional WhatsApp options

        Returns:
            True if sent successfully
        """
        if not self.client or not self._ready:
            logger.error("WhatsApp client not ready")
            return False

        try:
            await self.client.send_message(
                chat_id=chat_id,
                text=content,
                quoted_msg_id=reply_to,
                **kwargs
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send WhatsApp message: {e}")
            return False

    async def send_typing_indicator(self, chat_id: str):
        """Send typing indicator"""
        if self.client and self._ready:
            try:
                await self.client.send_seen(chat_id)
                await self.client.start_typing(chat_id)
            except Exception as e:
                logger.error(f"Failed to send typing indicator: {e}")

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        **kwargs
    ) -> bool:
        """Send an image via WhatsApp"""
        if not self.client or not self._ready:
            return False

        try:
            await self.client.send_image(
                chat_id=chat_id,
                image=image_url,
                caption=caption,
                **kwargs
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send image: {e}")
            return False

    async def send_file(
        self,
        chat_id: str,
        file_url: str,
        filename: Optional[str] = None,
        **kwargs
    ) -> bool:
        """Send a file via WhatsApp"""
        if not self.client or not self._ready:
            return False

        try:
            await self.client.send_document(
                chat_id=chat_id,
                document=file_url,
                filename=filename,
                **kwargs
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send file: {e}")
            return False

    # Event handlers
    async def _on_ready(self):
        """Handle ready event"""
        self._ready = True
        self.status = ChannelStatus.CONNECTED
        await self.emit("connected")
        logger.info("✓ WhatsApp client ready")

    async def _on_qr(self, qr_code: str):
        """Handle QR code event"""
        logger.info("WhatsApp QR Code received")
        logger.info("Scan this QR code with your phone:")
        logger.info(f"\n{qr_code}\n")

        # Emit QR code event so GUI can display it
        await self.emit("qr_code", qr_code=qr_code)

    async def _on_disconnected(self, reason: str):
        """Handle disconnection"""
        logger.warning(f"WhatsApp disconnected: {reason}")
        self._ready = False
        self.status = ChannelStatus.DISCONNECTED
        await self.emit("disconnected", reason=reason)

    async def _on_message(self, msg):
        """Handle incoming message"""
        # Skip if message is from self
        if msg.from_me:
            return

        # Create user object
        contact = await msg.get_contact()
        user = ChannelUser(
            id=msg.from_id,
            username=msg.from_id,
            display_name=contact.name if contact else msg.from_id
        )

        # Create message object
        message = ChannelMessage(
            message_id=msg.id,
            user=user,
            content=msg.body or "",
            message_type=MessageType.TEXT,
            channel_id="whatsapp",
            chat_id=msg.chat_id
        )

        logger.info(f"WhatsApp message from {user.full_name}: {message.content[:50]}...")

        # Emit message event
        await self.emit("message", message)

        if not self.agent:
            await msg.reply("❌ Agent not initialized")
            return

        # Send typing indicator
        await self.send_typing_indicator(msg.chat_id)

        # Process message
        response_text = ""
        try:
            async for chunk in self.agent.process_message(user.id, message.content):
                response_text += chunk

            if response_text:
                await msg.reply(response_text)
            else:
                await msg.reply("🤔 I'm not sure how to respond to that.")

        except Exception as e:
            logger.error(f"Error processing WhatsApp message: {e}", exc_info=True)
            await msg.reply("❌ Sorry, I encountered an error. Please try again.")

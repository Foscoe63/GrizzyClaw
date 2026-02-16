"""Telegram channel implementation"""

import asyncio
import logging
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from grizzyclaw.config import Settings
from grizzyclaw.agent.core import AgentCore
from .base import Channel, ChannelMessage, ChannelUser, MessageType, ChannelStatus

logger = logging.getLogger(__name__)


class TelegramChannel(Channel):
    """Telegram messaging channel"""

    def __init__(self, settings: Settings):
        """Initialize Telegram channel

        Args:
            settings: Application settings
        """
        config = {
            "bot_token": settings.telegram_bot_token,
            "webhook_url": settings.telegram_webhook_url
        }
        super().__init__("telegram", config)

        self.settings = settings
        self.application: Optional[Application] = None
        self.agent: Optional[AgentCore] = None

    async def start(self):
        """Start Telegram bot"""
        if not self.config.get("bot_token"):
            logger.warning("No Telegram bot token configured")
            self.status = ChannelStatus.ERROR
            return

        logger.info("Starting Telegram channel...")
        self.status = ChannelStatus.CONNECTING

        try:
            self.application = (
                Application.builder()
                .token(self.config["bot_token"])
                .build()
            )
            self.agent = AgentCore(self.settings)

            # Add handlers
            self.application.add_handler(CommandHandler("start", self.cmd_start))
            self.application.add_handler(CommandHandler("help", self.cmd_help))
            self.application.add_handler(CommandHandler("reset", self.cmd_reset))
            self.application.add_handler(CommandHandler("memory", self.cmd_memory))
            self.application.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message)
            )

            # Start bot
            if self.config.get("webhook_url"):
                await self.application.initialize()
                await self.application.start()
                await self.application.updater.start_webhook(
                    listen="0.0.0.0",
                    port=8443,
                    webhook_url=self.config["webhook_url"],
                )
            else:
                await self.application.initialize()
                await self.application.start()
                await self.application.updater.start_polling()

            self.status = ChannelStatus.CONNECTED
            await self.emit("connected")
            logger.info("✓ Telegram channel started successfully")

            # Keep event loop running - start_polling() returns immediately, so we must
            # wait here or the loop exits and the bot stops receiving messages
            self._stop_event = asyncio.Event()
            self._loop = asyncio.get_running_loop()
            await self._stop_event.wait()

            # Cleanup when stop requested
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()

        except Exception as e:
            logger.error(f"Failed to start Telegram channel: {e}", exc_info=True)
            self.status = ChannelStatus.ERROR
            await self.emit("error", error=str(e))
            raise

    async def stop(self):
        """Stop Telegram bot"""
        logger.info("Stopping Telegram channel...")

        if hasattr(self, "_stop_event") and self._stop_event:
            self._stop_event.set()
        elif self.application:
            try:
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()
            except Exception as e:
                logger.warning(f"Error during Telegram shutdown: {e}")

        self.status = ChannelStatus.DISCONNECTED
        await self.emit("disconnected")
        logger.info("✓ Telegram channel stopped")

    async def send_message(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        **kwargs
    ) -> bool:
        """Send a message via Telegram

        Args:
            chat_id: Telegram chat ID
            content: Message text
            reply_to: Message ID to reply to
            **kwargs: Additional Telegram options

        Returns:
            True if sent successfully
        """
        if not self.application:
            logger.error("Telegram application not initialized")
            return False

        try:
            await self.application.bot.send_message(
                chat_id=int(chat_id),
                text=content,
                reply_to_message_id=int(reply_to) if reply_to else None,
                **kwargs
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def send_typing_indicator(self, chat_id: str):
        """Send typing indicator"""
        if self.application:
            try:
                await self.application.bot.send_chat_action(
                    chat_id=int(chat_id),
                    action="typing"
                )
            except Exception as e:
                logger.error(f"Failed to send typing indicator: {e}")

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        **kwargs
    ) -> bool:
        """Send an image via Telegram"""
        if not self.application:
            return False

        try:
            await self.application.bot.send_photo(
                chat_id=int(chat_id),
                photo=image_url,
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
        """Send a file via Telegram"""
        if not self.application:
            return False

        try:
            await self.application.bot.send_document(
                chat_id=int(chat_id),
                document=file_url,
                filename=filename,
                **kwargs
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send file: {e}")
            return False

    # Command handlers
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        welcome_text = (
            "🐻 Welcome to GrizzyClaw!\n\n"
            "I'm your 24/7 AI assistant with memory. I can:\n"
            "- Answer questions\n"
            "- Remember our conversations\n"
            "- Connect to local & cloud LLMs\n\n"
            "Commands:\n"
            "/help - Show help\n"
            "/reset - Clear conversation\n"
            "/memory - Show your memory"
        )
        await update.message.reply_text(welcome_text)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_text = (
            "🐻 GrizzyClaw Help\n\n"
            "Just chat with me naturally!\n\n"
            "Commands:\n"
            "/start - Start the bot\n"
            "/help - Show this help\n"
            "/reset - Clear conversation history\n"
            "/memory - View your stored memories"
        )
        await update.message.reply_text(help_text)

    async def cmd_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /reset command"""
        user_id = str(update.effective_user.id)
        if self.agent:
            await self.agent.clear_session(user_id)
        await update.message.reply_text("🔄 Conversation reset. Let's start fresh!")

    async def cmd_memory(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /memory command"""
        user_id = str(update.effective_user.id)
        if not self.agent:
            await update.message.reply_text("❌ Agent not initialized")
            return

        memory = await self.agent.get_user_memory(user_id)

        if memory["total_items"] == 0:
            await update.message.reply_text("📝 No memories stored yet. Let's chat!")
            return

        text = f"🧠 Your Memory ({memory['total_items']} items)\n\n"
        text += "Recent memories:\n"
        for item in memory["recent_items"]:
            text += f"- {item['content'][:50]}...\n"

        await update.message.reply_text(text)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming text message"""
        if not update.message or not update.message.text:
            return

        user = ChannelUser(
            id=str(update.effective_user.id),
            username=update.effective_user.username,
            first_name=update.effective_user.first_name,
            last_name=update.effective_user.last_name
        )

        message = ChannelMessage(
            message_id=str(update.message.message_id),
            user=user,
            content=update.message.text,
            message_type=MessageType.TEXT,
            channel_id="telegram",
            chat_id=str(update.message.chat_id)
        )

        logger.info(f"Message from {user.full_name}: {message.content[:50]}...")

        # Emit message event
        await self.emit("message", message)

        if not self.agent:
            await update.message.reply_text("❌ Agent not initialized")
            return

        # Show typing indicator
        await self.send_typing_indicator(str(update.message.chat_id))

        # Process message
        response_text = ""
        try:
            async for chunk in self.agent.process_message(user.id, message.content):
                response_text += chunk

            if response_text:
                await update.message.reply_text(response_text)
            else:
                await update.message.reply_text(
                    "🤔 I'm not sure how to respond to that."
                )

        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            hint = (
                " Make sure your LLM (LM Studio, Ollama, etc.) is running."
                if "connection" in str(e).lower() or "refused" in str(e).lower()
                else ""
            )
            await update.message.reply_text(
                f"❌ Sorry, I encountered an error.{hint}\n\nPlease try again."
            )

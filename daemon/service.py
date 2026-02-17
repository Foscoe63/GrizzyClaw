"""Main daemon service for 24/7 background operation"""

import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Optional

from grizzyclaw.config import Settings
from grizzyclaw.observability.logging_config import setup_logging
from grizzyclaw.agent.core import AgentCore
from grizzyclaw.llm.router import LLMRouter
from grizzyclaw.gateway.server import GatewayServer
from grizzyclaw.gateway.http_server import HTTPServer
from grizzyclaw.daemon.ipc import IPCServer
from grizzyclaw.automation.webhooks import WebhookServer
from grizzyclaw.gateway.events import Event

logger = logging.getLogger(__name__)


class DaemonService:
    """Background service that runs 24/7"""

    def __init__(self, config_path: Optional[str] = None):
        """Initialize daemon service

        Args:
            config_path: Path to config file, defaults to ~/.grizzyclaw/config.yaml
        """
        self.config_path = config_path or str(Path.home() / ".grizzyclaw" / "config.yaml")
        self.settings: Optional[Settings] = None
        self.agent: Optional[AgentCore] = None
        self.llm_router: Optional[LLMRouter] = None
        self.gateway: Optional[GatewayServer] = None
        self.http_server: Optional[HTTPServer] = None
        self.webhook_server: Optional[WebhookServer] = None
        self.ipc_server: Optional[IPCServer] = None
        self.running = False
        self._tasks = []

        # Setup signal handlers
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    async def start(self):
        """Start the daemon service"""
        logger.info("Starting GrizzyClaw daemon service...")

        try:
            # Load configuration
            config_file = Path(self.config_path)
            if config_file.exists():
                self.settings = Settings.from_file(str(config_file))
                logger.info(f"Loaded configuration from {self.config_path}")
            else:
                self.settings = Settings()
                logger.warning("Config file not found, using defaults")

            # Setup structured logging if configured
            log_json = getattr(self.settings, "log_json", False)
            log_pii = getattr(self.settings, "log_pii_redact", True)
            setup_logging(
                level=getattr(self.settings, "log_level", "INFO"),
                json_format=log_json,
                pii_redact=log_pii,
                log_file=str(Path.home() / ".grizzyclaw" / "daemon.log"),
            )
            if getattr(self.settings, "tracing_enabled", False):
                from grizzyclaw.observability.tracing import init_tracing
                init_tracing(service_name="grizzyclaw-daemon")

            # Initialize LLM router
            self.llm_router = LLMRouter()
            self.llm_router.configure_from_settings(self.settings)
            logger.info("LLM router configured")

            # Initialize agent core
            self.agent = AgentCore(self.settings)
            logger.info("Agent core initialized")

            # Test LLM connections
            await self.llm_router.test_connections()

            # Start HTTP server (for WebChat UI)
            self.http_server = HTTPServer()
            await self.http_server.start()
            logger.info("HTTP server started")

            # Start Gateway server (WebSocket control plane)
            self.gateway = GatewayServer(self.settings)
            self.gateway.register_agent(self.agent)
            gateway_task = asyncio.create_task(self.gateway.start())
            self._tasks.append(gateway_task)
            logger.info("Gateway server started")

            # Start IPC server (for CLI/GUI communication)
            self.ipc_server = IPCServer()
            self._register_ipc_handlers()
            await self.ipc_server.start()
            logger.info("IPC server started")

            # Start webhook server for external triggers (GitHub, Slack, Zapier, etc.)
            self.webhook_server = WebhookServer(host="127.0.0.1", port=18790)
            self._register_webhook_handlers()
            await self.webhook_server.start()
            logger.info("Webhook server started")

            self.running = True
            logger.info("GrizzyClaw daemon service started successfully")

            # Start background tasks
            await self._run_event_loop()

        except Exception as e:
            logger.error(f"Failed to start daemon: {e}", exc_info=True)
            raise

    async def _run_event_loop(self):
        """Main event loop for daemon"""
        logger.info("Entering daemon event loop...")
        prune_interval = 3600  # 1 hour
        last_prune = 0.0
        import time

        try:
            while self.running:
                # Process any pending tasks
                await asyncio.sleep(1)

                # Media lifecycle: prune old assets periodically
                now = time.time()
                if now - last_prune >= prune_interval:
                    try:
                        from grizzyclaw.media.lifecycle import prune_media
                        retention = getattr(
                            self.settings, "media_retention_days", 7
                        )
                        max_mb = getattr(self.settings, "media_max_size_mb", 0)
                        prune_media(retention_days=retention, max_size_mb=max_mb)
                        last_prune = now
                    except Exception as e:
                        logger.debug(f"Media prune skipped: {e}")

        except Exception as e:
            logger.error(f"Error in event loop: {e}", exc_info=True)
            self.running = False

    async def stop(self):
        """Stop the daemon service gracefully"""
        logger.info("Stopping GrizzyClaw daemon service...")
        self.running = False

        # Stop HTTP server
        if self.http_server:
            await self.http_server.stop()

        # Stop gateway
        if self.gateway:
            await self.gateway.stop()

        # Stop webhook server
        if self.webhook_server:
            await self.webhook_server.stop()

        # Stop IPC server
        if self.ipc_server:
            await self.ipc_server.stop()

        # Cancel all background tasks
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        logger.info("GrizzyClaw daemon service stopped")

    async def reload_config(self):
        """Reload configuration without restarting"""
        logger.info("Reloading configuration...")
        try:
            config_file = Path(self.config_path)
            if config_file.exists():
                self.settings = Settings.from_file(str(config_file))
                # Reconfigure LLM router
                self.llm_router.configure_from_settings(self.settings)
                logger.info("Configuration reloaded successfully")
            else:
                logger.warning("Config file not found, keeping current config")
        except Exception as e:
            logger.error(f"Failed to reload config: {e}")

    def _register_ipc_handlers(self):
        """Register IPC command handlers"""
        self.ipc_server.register_handler("status", self._handle_status)
        self.ipc_server.register_handler("reload", self._handle_reload)
        self.ipc_server.register_handler("stats", self._handle_stats)
        self.ipc_server.register_handler("sessions_list", self._handle_sessions_list)
        self.ipc_server.register_handler("sessions_history", self._handle_sessions_history)
        self.ipc_server.register_handler("sessions_send", self._handle_sessions_send)
        self.ipc_server.register_handler("stop", self._handle_stop)

    def _register_webhook_handlers(self):
        """Register webhook handlers for external triggers."""
        async def handle_trigger(data: dict):
            """Process webhook payload: send message to agent and return response."""
            message = data.get("message") or data.get("text") or data.get("body", "")
            if isinstance(message, dict):
                message = message.get("text", str(message))
            if not message:
                return {"status": "ignored", "reason": "No message in payload"}
            user_id = data.get("user_id") or data.get("source", "webhook")
            try:
                response_chunks = []
                async for chunk in self.agent.process_message(user_id, str(message)):
                    response_chunks.append(chunk)
                return {"status": "ok", "response": "".join(response_chunks)[:500]}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        self.webhook_server.register("/trigger", handle_trigger, metadata={"description": "Send message to agent"})
        self.webhook_server.register("/github", self._handle_github_webhook, metadata={"description": "GitHub webhooks"})
        self.webhook_server.register("/slack", self._handle_slack_webhook, metadata={"description": "Slack events"})
        from grizzyclaw.automation.pubsub import verify_pubsub_push
        gmail_audience = getattr(self.settings, "gmail_pubsub_audience", None)
        self.webhook_server.register(
            "/gmail",
            self._handle_gmail_pubsub,
            verify=verify_pubsub_push(gmail_audience or ""),
            metadata={"description": "Gmail Pub/Sub push"},
        )
        self.webhook_server.register("/media", self._handle_media_webhook, metadata={"description": "Media upload; transcribe and forward to agent"})

    async def _handle_github_webhook(self, data: dict):
        """Handle GitHub webhook (push, issues, etc.). Event type is in X-GitHub-Event header (not in body)."""
        action = data.get("action", "")
        repo = data.get("repository", {}).get("full_name", "unknown")
        msg = f"GitHub webhook for {repo}" + (f": {action}" if action else "")
        if self.agent:
            chunks = []
            async for c in self.agent.process_message("github", msg):
                chunks.append(c)
            return {"status": "ok", "summary": "".join(chunks)[:200]}
        return {"status": "ok"}

    async def _handle_media_webhook(self, data: dict):
        """Handle media upload: transcribe audio and forward to agent."""
        audio_b64 = data.get("audio_base64") or data.get("audio")
        if not audio_b64:
            return {"status": "error", "error": "audio_base64 or audio required"}
        user_id = data.get("user_id", "webhook")
        try:
            response_chunks = []
            async for chunk in self.agent.process_message(
                user_id, "", audio_base64=audio_b64
            ):
                response_chunks.append(chunk)
            return {"status": "ok", "response": "".join(response_chunks)[:500]}
        except Exception as e:
            logger.error(f"Media webhook error: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    async def _handle_gmail_pubsub(self, data: dict):
        """Handle Gmail Pub/Sub push notifications."""
        try:
            from grizzyclaw.automation.pubsub import handle_gmail_push
            creds = getattr(self.settings, "gmail_credentials_json", None)
            async def agent_cb(uid, msg):
                async for c in self.agent.process_message(uid, msg):
                    yield c
            return await handle_gmail_push(
                data, agent_cb,
                credentials_path=creds,
                secret_key=getattr(self.settings, "secret_key", None),
            )
        except Exception as e:
            logger.error(f"Gmail Pub/Sub error: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    async def _handle_slack_webhook(self, data: dict):
        """Handle Slack events (e.g. slash command, event callback)."""
        if data.get("type") == "url_verification":
            return {"challenge": data.get("challenge", "")}
        text = data.get("text") or data.get("message", {}).get("text", "")
        if text and self.agent:
            chunks = []
            async for c in self.agent.process_message("slack", str(text)):
                chunks.append(c)
            return {"status": "ok", "response": "".join(chunks)[:500]}
        return {"status": "ok"}

    async def _handle_status(self) -> dict:
        """Handle status command"""
        return {
            "running": self.running,
            "sessions": len(self.gateway.session_manager.sessions) if self.gateway else 0,
            "channels": len(self.gateway.channels) if self.gateway else 0
        }

    async def _handle_reload(self) -> dict:
        """Handle reload command"""
        await self.reload_config()
        return {"status": "reloaded"}

    async def _handle_stats(self) -> dict:
        """Handle stats command"""
        if self.gateway:
            return self.gateway.stats
        return {}

    async def _handle_sessions_list(self, user_id: Optional[str] = None) -> dict:
        """Handle sessions_list command. Returns list of sessions, optionally filtered by user_id."""
        if not self.gateway:
            return {"sessions": []}
        sessions = self.gateway.session_manager.list_sessions()
        if user_id:
            sessions = [s for s in sessions if s.get("user_id") == user_id]
        return {"sessions": sessions}

    async def _handle_sessions_history(
        self, session_id: Optional[str] = None, limit: Optional[int] = None
    ) -> dict:
        """Handle sessions_history command. Returns message history for a session."""
        if not session_id:
            return {"history": [], "error": "session_id required"}
        if not self.gateway:
            return {"history": [], "error": "Gateway not available"}
        session = self.gateway.session_manager.get_session(session_id)
        if not session:
            return {"history": [], "error": f"Session {session_id} not found"}
        history = session.get_history(limit=limit)
        return {"history": history}

    async def _handle_sessions_send(
        self,
        session_id: str,
        message: str,
        from_agent_id: Optional[str] = None,
    ) -> dict:
        """Handle sessions_send command. Routes message to session; agent processes and responds."""
        if not self.gateway or not self.agent:
            return {"status": "error", "error": "Gateway or agent not available"}
        session = self.gateway.session_manager.get_session(session_id)
        if not session:
            session = self.gateway.session_manager.create_session(
                session_id, from_agent_id or "ipc_agent"
            )
        user_id = session.user_id
        self.gateway.session_manager.record_message(
            session_id, "user", message,
            metadata={"from_agent_id": from_agent_id} if from_agent_id else None,
        )
        try:
            response_chunks = []
            async for chunk in self.agent.process_message(user_id, message):
                response_chunks.append(chunk)
            response_text = "".join(response_chunks)
            self.gateway.session_manager.record_message(session_id, "assistant", response_text)
            await self.gateway.event_bus.emit(
                Event(
                    type="message_sent",
                    data={
                        "session_id": session_id,
                        "user_id": user_id,
                        "message": response_text,
                    },
                )
            )
            return {"status": "ok", "response": response_text}
        except Exception as e:
            logger.error(f"sessions_send error: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    async def _handle_stop(self) -> dict:
        """Handle stop command: gracefully shut down the daemon."""
        logger.info("Received stop command via IPC")
        self.running = False
        return {"status": "ok"}


def main():
    """Main entry point for daemon"""
    daemon = DaemonService()

    try:
        asyncio.run(daemon.start())
    except KeyboardInterrupt:
        logger.info("Daemon interrupted by user")
    except Exception as e:
        logger.error(f"Daemon crashed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

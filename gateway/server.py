"""WebSocket gateway server - Central control plane"""

import asyncio
import json
import logging
from typing import Dict, Set, Optional, Any
from pathlib import Path
import websockets
from websockets.server import WebSocketServerProtocol

from grizzyclaw.config import Settings
from .session import SessionManager
from .events import EventBus, Event

logger = logging.getLogger(__name__)


class GatewayServer:
    """WebSocket server for centralized control plane

    Runs on ws://127.0.0.1:18789 (like openclaw)
    Manages sessions, channels, events, and provides WebChat interface
    """

    def __init__(self, settings: Settings, host: str = "127.0.0.1", port: int = 18789):
        """Initialize gateway server

        Args:
            settings: Application settings
            host: Host to bind to (default: localhost)
            port: Port to listen on (default: 18789)
        """
        self.settings = settings
        self.host = host
        self.port = port

        # Core components
        self.session_manager = SessionManager()
        self.event_bus = EventBus()

        # Connected WebSocket clients
        self.clients: Set[WebSocketServerProtocol] = set()

        # Session subscription tracking: session_id -> set of websockets
        self.session_subscribers: Dict[str, Set[WebSocketServerProtocol]] = {}

        # Optional agent for processing messages (set via register_agent)
        self.agent: Optional[Any] = None

        # Channel registry
        self.channels: Dict[str, Any] = {}

        # Server instance
        self.server = None

        # Stats
        self.stats = {
            "total_messages": 0,
            "total_sessions": 0,
            "uptime_start": None
        }

    async def start(self):
        """Start the gateway server"""
        logger.info(f"Starting Gateway server on ws://{self.host}:{self.port}")

        # Register event handlers
        self._register_event_handlers()

        # Start WebSocket server
        self.server = await websockets.serve(
            self._handle_client,
            self.host,
            self.port
        )

        # Start stats
        import time
        self.stats["uptime_start"] = time.time()

        logger.info(f"✓ Gateway server started on ws://{self.host}:{self.port}")
        logger.info("  WebChat UI: http://127.0.0.1:18789/chat")
        logger.info("  Control UI: http://127.0.0.1:18789/control")

    async def stop(self):
        """Stop the gateway server"""
        logger.info("Stopping Gateway server...")

        # Close all client connections
        if self.clients:
            await asyncio.gather(
                *[client.close() for client in self.clients],
                return_exceptions=True
            )

        # Stop server
        if self.server:
            self.server.close()
            await self.server.wait_closed()

        logger.info("Gateway server stopped")

    async def _handle_client(self, websocket: WebSocketServerProtocol, path: str):
        """Handle incoming WebSocket connection"""
        self.clients.add(websocket)
        client_id = id(websocket)

        logger.info(f"Client {client_id} connected from {websocket.remote_address}")

        try:
            # Send welcome message
            await self._send_to_client(websocket, {
                "type": "welcome",
                "message": "Connected to GrizzyClaw Gateway",
                "version": "0.1.0"
            })

            # Handle messages
            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self._handle_message(websocket, data)
                except json.JSONDecodeError:
                    await self._send_error(websocket, "Invalid JSON")
                except Exception as e:
                    logger.error(f"Error handling message: {e}", exc_info=True)
                    await self._send_error(websocket, str(e))

        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Client {client_id} disconnected")
        finally:
            self.clients.discard(websocket)
            self._unsubscribe_client(websocket)

    async def _handle_message(self, websocket: WebSocketServerProtocol, data: Dict):
        """Handle incoming message from client"""
        msg_type = data.get("type")

        if msg_type == "ping":
            await self._send_to_client(websocket, {"type": "pong"})

        elif msg_type == "chat_message":
            # Handle chat message from WebChat
            await self._handle_chat_message(websocket, data)

        elif msg_type == "subscribe_session":
            # Subscribe to session updates
            session_id = data.get("session_id")
            await self._subscribe_session(websocket, session_id)

        elif msg_type == "get_sessions":
            # Get all active sessions
            await self._send_sessions(websocket)

        elif msg_type == "get_stats":
            # Get gateway stats
            await self._send_stats(websocket)

        elif msg_type == "typing":
            # Typing indicator
            await self._handle_typing(data)

        else:
            await self._send_error(websocket, f"Unknown message type: {msg_type}")

    async def _handle_chat_message(self, websocket: WebSocketServerProtocol, data: Dict):
        """Handle chat message from WebChat client"""
        user_id = data.get("user_id", "webchat_user")
        message_text = data.get("message", "")
        session_id = data.get("session_id", "default")

        if not message_text:
            return

        # Get or create session
        session = self.session_manager.get_or_create(session_id, user_id)

        # Add user message to session
        session.add_message("user", message_text)

        # Broadcast to all clients
        await self._broadcast({
            "type": "chat_message",
            "session_id": session_id,
            "user_id": user_id,
            "message": message_text,
            "timestamp": session.messages[-1]["timestamp"]
        })

        # Update stats
        self.stats["total_messages"] += 1

        # Emit event for processing
        await self.event_bus.emit(Event(
            type="message_received",
            data={
                "session_id": session_id,
                "user_id": user_id,
                "message": message_text
            }
        ))

    async def _subscribe_session(self, websocket: WebSocketServerProtocol, session_id: str):
        """Subscribe client to session updates."""
        if session_id not in self.session_subscribers:
            self.session_subscribers[session_id] = set()
        self.session_subscribers[session_id].add(websocket)
        logger.info(f"Client subscribed to session: {session_id}")
        await self._send_to_client(websocket, {"type": "subscribed", "session_id": session_id})

    def _unsubscribe_client(self, websocket: WebSocketServerProtocol):
        """Remove client from all session subscriptions."""
        for subs in self.session_subscribers.values():
            subs.discard(websocket)

    async def _send_sessions(self, websocket: WebSocketServerProtocol):
        """Send all active sessions to client"""
        sessions = self.session_manager.list_sessions()
        await self._send_to_client(websocket, {
            "type": "sessions",
            "sessions": sessions
        })

    async def _send_stats(self, websocket: WebSocketServerProtocol):
        """Send gateway statistics to client"""
        import time
        uptime = time.time() - self.stats["uptime_start"] if self.stats["uptime_start"] else 0

        await self._send_to_client(websocket, {
            "type": "stats",
            "stats": {
                "total_messages": self.stats["total_messages"],
                "total_sessions": len(self.session_manager.sessions),
                "active_clients": len(self.clients),
                "active_channels": len(self.channels),
                "uptime_seconds": uptime
            }
        })

    async def _handle_typing(self, data: Dict):
        """Handle typing indicator"""
        # Broadcast typing status to all clients
        await self._broadcast({
            "type": "typing",
            "session_id": data.get("session_id"),
            "user_id": data.get("user_id"),
            "is_typing": data.get("is_typing", False)
        })

    async def _send_to_client(self, websocket: WebSocketServerProtocol, data: Dict):
        """Send message to specific client"""
        try:
            await websocket.send(json.dumps(data))
        except Exception as e:
            logger.error(f"Failed to send to client: {e}")

    async def _send_error(self, websocket: WebSocketServerProtocol, error: str):
        """Send error message to client"""
        await self._send_to_client(websocket, {
            "type": "error",
            "error": error
        })

    async def _broadcast(self, data: Dict):
        """Broadcast message to all connected clients"""
        if self.clients:
            await asyncio.gather(
                *[self._send_to_client(client, data) for client in self.clients],
                return_exceptions=True
            )

    def _register_event_handlers(self):
        """Register event handlers"""
        # Handle message events
        self.event_bus.on("message_received", self._on_message_received)
        self.event_bus.on("message_sent", self._on_message_sent)

    async def _on_message_received(self, event: Event):
        """Handle message received - forward to agent and broadcast response."""
        session_id = event.data.get("session_id", "default")
        user_id = event.data.get("user_id", "webchat_user")
        message = event.data.get("message", "")
        logger.info(f"Message received: {message[:50]}...")

        if self.agent:
            try:
                response_text = ""
                async for chunk in self.agent.process_message(user_id, message):
                    response_text += chunk
                session = self.session_manager.get_or_create(session_id, user_id)
                session.add_message("assistant", response_text)
                await self.event_bus.emit(Event(
                    type="message_sent",
                    data={
                        "session_id": session_id,
                        "user_id": user_id,
                        "message": response_text,
                        "timestamp": session.messages[-1]["timestamp"],
                    },
                ))
            except Exception as e:
                logger.error(f"Agent processing failed: {e}", exc_info=True)
                await self._broadcast_to_session(session_id, {
                    "type": "error",
                    "error": str(e),
                })

    async def _on_message_sent(self, event: Event):
        """Handle message sent event - broadcast to session subscribers."""
        await self._broadcast_to_session(
            event.data.get("session_id", "default"),
            {
                "type": "assistant_message",
                "session_id": event.data.get("session_id"),
                "message": event.data.get("message"),
                "timestamp": event.data.get("timestamp"),
            },
        )

    async def _broadcast_to_session(self, session_id: str, data: Dict):
        """Send message to clients subscribed to this session (or all if none)."""
        subs = self.session_subscribers.get(session_id, set())
        targets = subs if subs else self.clients
        if targets:
            await asyncio.gather(
                *[self._send_to_client(ws, data) for ws in targets],
                return_exceptions=True,
            )

    def register_agent(self, agent: Any):
        """Register agent for processing chat messages."""
        self.agent = agent
        logger.info("Agent registered for message processing")

    def register_channel(self, name: str, channel: Any):
        """Register a messaging channel

        Args:
            name: Channel name (e.g., 'telegram', 'whatsapp')
            channel: Channel instance
        """
        self.channels[name] = channel
        logger.info(f"Registered channel: {name}")

    def unregister_channel(self, name: str):
        """Unregister a messaging channel"""
        if name in self.channels:
            del self.channels[name]
            logger.info(f"Unregistered channel: {name}")

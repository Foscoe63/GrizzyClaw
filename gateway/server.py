"""WebSocket gateway server - Central control plane"""

import asyncio
import json
import logging
import time
from typing import Dict, Set, Optional, Any
from pathlib import Path
import websockets
from websockets.server import WebSocketServerProtocol

from grizzyclaw.config import Settings
from grizzyclaw.security import RateLimiter
from .session import SessionManager
from .session_store import SessionStore
from .presence_store import load_presence, save_presence
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

        # Core components (session store for persistence)
        self.session_store = SessionStore()
        self.session_manager = SessionManager(session_store=self.session_store)
        self.event_bus = EventBus()

        # Connected WebSocket clients
        self.clients: Set[WebSocketServerProtocol] = set()

        # Session subscription tracking: session_id -> set of websockets
        self.session_subscribers: Dict[str, Set[WebSocketServerProtocol]] = {}
        # Client -> user_id for presence (set when client identifies)
        self.client_user_ids: Dict[int, str] = {}

        # Optional agent for processing messages (set via register_agent)
        self.agent: Optional[Any] = None
        self.agent_queue: Optional[Any] = None

        # Presence: user_id -> last_seen timestamp (loaded from persistence)
        self.presence: Dict[str, float] = {}
        try:
            self.presence = load_presence()
        except Exception as e:
            logger.debug(f"Could not load presence: {e}")

        # Rate limiter per client
        rl_req = getattr(settings, "gateway_rate_limit_requests", 60)
        rl_win = getattr(settings, "gateway_rate_limit_window", 60)
        self.rate_limiter = RateLimiter(max_requests=rl_req, window=rl_win)

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
        logger.info("  WebChat UI: http://127.0.0.1:18788/chat")
        logger.info("  Control UI: http://127.0.0.1:18788/control")

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
            await self._emit_client_connected(websocket)

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
            await self._emit_client_disconnected(websocket)

    async def _handle_message(self, websocket: WebSocketServerProtocol, data: Dict):
        """Handle incoming message from client"""
        msg_type = data.get("type")

        # Rate limit (skip for ping/pong)
        if msg_type != "ping":
            key = str(id(websocket))
            if not self.rate_limiter.is_allowed(key):
                await self._send_error(websocket, "Rate limit exceeded")
                return

        if msg_type == "ping":
            await self._send_to_client(websocket, {"type": "pong"})

        elif msg_type == "identify":
            user_id = data.get("user_id", "")
            if user_id:
                self.client_user_ids[id(websocket)] = user_id
                ts = time.time()
                self.presence[user_id] = ts
                try:
                    save_presence(user_id, ts)
                except Exception as e:
                    logger.debug(f"Could not save presence: {e}")
                await self.event_bus.emit(Event(
                    type="user_online",
                    data={"user_id": user_id},
                ))
                # Auto-subscribe to group sessions where user is participant
                for sid, session in self.session_manager.sessions.items():
                    if session.session_type == "group" and user_id in session.participants:
                        if sid not in self.session_subscribers:
                            self.session_subscribers[sid] = set()
                        self.session_subscribers[sid].add(websocket)
                await self._send_to_client(websocket, {"type": "identified", "user_id": user_id})
            else:
                await self._send_error(websocket, "user_id required for identify")

        elif msg_type == "chat_message":
            # Handle chat message from WebChat
            await self._handle_chat_message(websocket, data)

        elif msg_type == "subscribe_session":
            # Subscribe to session updates
            session_id = data.get("session_id")
            await self._subscribe_session(websocket, session_id)

        elif msg_type == "get_sessions" or msg_type == "sessions_list":
            # Get all active sessions
            await self._send_sessions(websocket)

        elif msg_type == "sessions_history":
            session_id = data.get("session_id")
            limit = data.get("limit")
            if limit is not None:
                limit = int(limit) if isinstance(limit, (int, str)) and str(limit).isdigit() else None
            await self._send_session_history(websocket, session_id, limit)

        elif msg_type == "sessions_send":
            await self._handle_sessions_send(websocket, data)

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

        # Add user message to session (persisted)
        self.session_manager.record_message(session_id, "user", message_text)

        # Broadcast to all clients
        await self._broadcast({
            "type": "chat_message",
            "session_id": session_id,
            "user_id": user_id,
            "message": message_text,
            "timestamp": session.messages[-1].timestamp
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

    async def _send_session_history(
        self,
        websocket: WebSocketServerProtocol,
        session_id: Optional[str],
        limit: Optional[int] = None,
    ):
        """Send message history for a session to client"""
        if not session_id:
            await self._send_error(websocket, "session_id required")
            return
        session = self.session_manager.get_session(session_id)
        if not session:
            await self._send_to_client(websocket, {
                "type": "session_history",
                "session_id": session_id,
                "history": [],
                "error": f"Session {session_id} not found",
            })
            return
        history = session.get_history(limit=limit)
        await self._send_to_client(websocket, {
            "type": "session_history",
            "session_id": session_id,
            "history": history,
        })

    def _check_gateway_auth(self, data: Dict) -> bool:
        """Check if client is authenticated for sensitive operations."""
        token = getattr(self.settings, "gateway_auth_token", None)
        if not token:
            return True
        return data.get("token") == token

    async def _handle_sessions_send(
        self, websocket: WebSocketServerProtocol, data: Dict
    ):
        """Handle sessions_send: route message to session, agent processes, stream response."""
        if not self._check_gateway_auth(data):
            await self._send_error(websocket, "Unauthorized: invalid or missing token")
            return
        session_id = data.get("session_id")
        message = data.get("message", "")
        if not session_id or not message:
            await self._send_error(websocket, "session_id and message required")
            return
        session = self.session_manager.get_session(session_id)
        if not session:
            user_id = data.get("from_agent_id") or "ws_agent"
            session = self.session_manager.create_session(session_id, user_id)
        user_id = session.user_id
        self.session_manager.record_message(
            session_id,
            "user",
            message,
            metadata={"from_agent_id": data.get("from_agent_id")} if data.get("from_agent_id") else None,
        )
        if not self.agent:
            await self._send_error(websocket, "Agent not available")
            return
        try:
            response_text = ""
            async for chunk in self.agent.process_message(user_id, message):
                response_text += chunk
            self.session_manager.record_message(session_id, "assistant", response_text)
            await self.event_bus.emit(Event(
                type="message_sent",
                data={
                    "session_id": session_id,
                    "user_id": user_id,
                    "message": response_text,
                },
            ))
            await self._send_to_client(websocket, {
                "type": "sessions_send_result",
                "session_id": session_id,
                "response": response_text,
                "status": "ok",
            })
        except Exception as e:
            logger.error(f"sessions_send error: {e}", exc_info=True)
            await self._send_error(websocket, str(e))

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
        """Handle typing indicator - broadcast only to session subscribers."""
        session_id = data.get("session_id")
        payload = {
            "type": "typing",
            "session_id": session_id,
            "user_id": data.get("user_id"),
            "is_typing": data.get("is_typing", False),
        }
        if session_id:
            await self._broadcast_to_session(session_id, payload)
        else:
            await self._broadcast(payload)

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
                process_fn = self.agent.process_message
                if self.agent_queue:
                    process_fn = self.agent_queue.process_message
                    response_text = ""
                    async for chunk in process_fn(session_id, user_id, message):
                        response_text += chunk
                else:
                    response_text = ""
                    async for chunk in self.agent.process_message(user_id, message):
                        response_text += chunk
                session = self.session_manager.get_or_create(session_id, user_id)
                self.session_manager.record_message(session_id, "assistant", response_text)
                await self.event_bus.emit(Event(
                    type="message_sent",
                    data={
                        "session_id": session_id,
                        "user_id": user_id,
                        "message": response_text,
                        "timestamp": session.messages[-1].timestamp,
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

    async def _emit_client_connected(self, websocket: WebSocketServerProtocol):
        """Emit client connected event for presence."""
        import time
        self.presence[f"client_{id(websocket)}"] = time.time()
        await self.event_bus.emit(Event(
            type="client_connected",
            data={"client_id": id(websocket)},
        ))

    async def _emit_client_disconnected(self, websocket: WebSocketServerProtocol):
        """Emit client disconnected event for presence."""
        cid = id(websocket)
        if f"client_{cid}" in self.presence:
            del self.presence[f"client_{cid}"]
        user_id = self.client_user_ids.pop(cid, None)
        if user_id:
            ts = time.time()
            self.presence[user_id] = ts
            try:
                save_presence(user_id, ts)
            except Exception as e:
                logger.debug(f"Could not save presence: {e}")
            await self.event_bus.emit(Event(
                type="user_offline",
                data={"user_id": user_id},
            ))
        await self.event_bus.emit(Event(
            type="client_disconnected",
            data={"client_id": cid},
        ))

    def register_agent(self, agent: Any):
        """Register agent for processing chat messages."""
        self.agent = agent
        queue_enabled = getattr(self.settings, "queue_enabled", False)
        if queue_enabled:
            from grizzyclaw.agent.queue import AgentQueue
            max_per = getattr(self.settings, "queue_max_per_session", 50)
            self.agent_queue = AgentQueue(agent, max_per_session=max_per)
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

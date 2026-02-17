"""Session management for gateway"""

import logging
import time
from typing import Dict, List, Optional, Any, TYPE_CHECKING
from dataclasses import dataclass, field
from datetime import datetime

if TYPE_CHECKING:
    from .session_store import SessionStore

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """Message in a session"""
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata
        }


@dataclass
class Session:
    """Conversation session

    Tracks messages, user info, and session state.
    session_type: "main" (1:1) or "group"
    """
    session_id: str
    user_id: str
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    messages: List[Message] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    session_type: str = "main"  # "main" (1:1) or "group"
    participants: List[str] = field(default_factory=list)  # For group sessions

    def add_message(self, role: str, content: str, metadata: Optional[Dict] = None):
        """Add a message to the session

        Args:
            role: 'user' or 'assistant'
            content: Message content
            metadata: Optional metadata
        """
        message = Message(
            role=role,
            content=content,
            metadata=metadata or {}
        )
        self.messages.append(message)
        self.last_activity = time.time()

    def get_history(self, limit: Optional[int] = None) -> List[Dict]:
        """Get message history

        Args:
            limit: Maximum number of messages to return

        Returns:
            List of message dictionaries
        """
        messages = self.messages[-limit:] if limit else self.messages
        return [msg.to_dict() for msg in messages]

    def get_context(self, max_tokens: int = 4000) -> List[Dict]:
        """Get context for LLM with token limit

        Args:
            max_tokens: Maximum tokens (approximate)

        Returns:
            List of message dictionaries within token limit
        """
        # Simple heuristic: ~4 chars per token
        max_chars = max_tokens * 4
        total_chars = 0
        context = []

        # Add messages from most recent backwards
        for msg in reversed(self.messages):
            msg_dict = msg.to_dict()
            msg_chars = len(msg_dict["content"])

            if total_chars + msg_chars > max_chars:
                break

            context.insert(0, msg_dict)
            total_chars += msg_chars

        return context

    def clear_history(self):
        """Clear message history"""
        self.messages.clear()

    def to_dict(self) -> Dict:
        """Convert session to dictionary"""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "message_count": len(self.messages),
            "is_active": self.is_active,
            "metadata": self.metadata,
            "session_type": self.session_type,
            "participants": self.participants,
        }


class SessionManager:
    """Manage all active sessions (optionally persisted to SQLite)"""

    def __init__(self, session_store: Optional["SessionStore"] = None):
        self.sessions: Dict[str, Session] = {}
        self._store = session_store
        if self._store:
            self._load_sessions()

    def create_session(
        self,
        session_id: str,
        user_id: str,
        session_type: str = "main",
        participants: Optional[List[str]] = None,
    ) -> Session:
        """Create a new session

        Args:
            session_id: Unique session ID
            user_id: User identifier (primary user for main, creator for group)
            session_type: "main" (1:1) or "group"
            participants: For group sessions, list of user_ids

        Returns:
            New session instance
        """
        if session_id in self.sessions:
            logger.warning(f"Session {session_id} already exists")
            return self.sessions[session_id]

        session = Session(
            session_id=session_id,
            user_id=user_id,
            session_type=session_type,
            participants=participants or ([user_id] if session_type == "group" else []),
        )
        self.sessions[session_id] = session

        if self._store:
            self._store.save_session(
                session_id=session_id,
                user_id=user_id,
                session_type=session_type,
                participants=session.participants,
                created_at=session.created_at,
                last_activity=session.last_activity,
            )

        logger.info(f"Created session: {session_id} for user: {user_id} (type={session_type})")
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Get existing session

        Args:
            session_id: Session ID

        Returns:
            Session if exists, None otherwise
        """
        return self.sessions.get(session_id)

    def get_or_create(self, session_id: str, user_id: str) -> Session:
        """Get existing session or create new one

        Args:
            session_id: Session ID
            user_id: User ID

        Returns:
            Session instance
        """
        session = self.get_session(session_id)
        if session is None and self._store:
            stored = self._store.get_session(session_id)
            if stored:
                session = self._hydrate_session(stored)
                self.sessions[session_id] = session
        if session is None:
            session = self.create_session(session_id, user_id)
        return session

    def _load_sessions(self):
        """Load sessions from store into memory (on startup)."""
        if not self._store:
            return
        for row in self._store.list_sessions():
            sid = row["session_id"]
            stored = self._store.get_session(sid)
            if stored:
                session = self._hydrate_session(stored)
                self.sessions[sid] = session

    def _hydrate_session(self, row: Dict[str, Any]) -> Session:
        """Build Session from stored row."""
        session_id = row["session_id"]
        messages = self._store.get_messages(session_id) if self._store else []
        session = Session(
            session_id=session_id,
            user_id=row["user_id"],
            created_at=row.get("created_at", time.time()),
            last_activity=row.get("last_activity", time.time()),
            messages=[
                Message(
                    role=m["role"],
                    content=m["content"],
                    timestamp=m["timestamp"],
                    metadata=m.get("metadata", {}),
                )
                for m in messages
            ],
            metadata=row.get("metadata", {}),
            is_active=row.get("is_active", True),
            session_type=row.get("session_type", "main"),
            participants=row.get("participants", []),
        )
        return session

    def record_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict] = None,
    ):
        """Add message to session and persist to store."""
        session = self.get_session(session_id)
        if session:
            session.add_message(role, content, metadata)
            if self._store:
                self._store.add_message(
                    session_id, role, content,
                    timestamp=session.messages[-1].timestamp,
                    metadata=metadata,
                )

    def delete_session(self, session_id: str) -> bool:
        """Delete a session

        Args:
            session_id: Session ID

        Returns:
            True if deleted, False if not found
        """
        if session_id in self.sessions:
            del self.sessions[session_id]
            logger.info(f"Deleted session: {session_id}")
            return True
        return False

    def list_sessions(self) -> List[Dict]:
        """List all sessions

        Returns:
            List of session dictionaries
        """
        return [session.to_dict() for session in self.sessions.values()]

    def get_user_sessions(self, user_id: str) -> List[Session]:
        """Get all sessions for a user

        Args:
            user_id: User identifier

        Returns:
            List of sessions
        """
        return [
            session for session in self.sessions.values()
            if session.user_id == user_id
        ]

    def prune_inactive(self, max_age_seconds: int = 86400):
        """Remove inactive sessions

        Args:
            max_age_seconds: Maximum age in seconds (default: 24 hours)
        """
        current_time = time.time()
        to_delete = []

        for session_id, session in self.sessions.items():
            age = current_time - session.last_activity
            if age > max_age_seconds:
                to_delete.append(session_id)

        for session_id in to_delete:
            self.delete_session(session_id)

        if to_delete:
            logger.info(f"Pruned {len(to_delete)} inactive sessions")

    def get_stats(self) -> Dict[str, Any]:
        """Get session statistics

        Returns:
            Statistics dictionary
        """
        total_messages = sum(len(s.messages) for s in self.sessions.values())
        active_sessions = sum(1 for s in self.sessions.values() if s.is_active)

        return {
            "total_sessions": len(self.sessions),
            "active_sessions": active_sessions,
            "total_messages": total_messages,
            "average_messages_per_session": total_messages / len(self.sessions) if self.sessions else 0
        }

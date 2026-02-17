"""SQLite-backed session persistence - single source of truth for Gateway sessions"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".grizzyclaw" / "sessions.db"


class SessionStore:
    """Persist sessions and messages to SQLite."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = str(db_path or DEFAULT_DB_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    session_type TEXT DEFAULT 'main',
                    participants TEXT,
                    created_at REAL NOT NULL,
                    last_activity REAL NOT NULL,
                    metadata TEXT,
                    is_active INTEGER DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    metadata TEXT,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_msg_session ON session_messages(session_id)")
            conn.commit()

    def save_session(
        self,
        session_id: str,
        user_id: str,
        session_type: str = "main",
        participants: Optional[List[str]] = None,
        created_at: Optional[float] = None,
        last_activity: Optional[float] = None,
        metadata: Optional[Dict] = None,
        is_active: bool = True,
    ):
        """Insert or replace session."""
        now = time.time()
        created_at = created_at or now
        last_activity = last_activity or now
        participants_json = json.dumps(participants or [])
        meta_json = json.dumps(metadata or {})
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_id, user_id, session_type, participants,
                    created_at, last_activity, metadata, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    user_id=excluded.user_id,
                    session_type=excluded.session_type,
                    participants=excluded.participants,
                    last_activity=excluded.last_activity,
                    metadata=excluded.metadata,
                    is_active=excluded.is_active
                """,
                (session_id, user_id, session_type, participants_json,
                 created_at, last_activity, meta_json, 1 if is_active else 0),
            )
            conn.commit()

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        timestamp: Optional[float] = None,
        metadata: Optional[Dict] = None,
    ):
        """Append message to session."""
        ts = timestamp or time.time()
        meta_json = json.dumps(metadata or {})
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO session_messages (session_id, role, content, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, role, content, ts, meta_json),
            )
            conn.execute(
                "UPDATE sessions SET last_activity = ? WHERE session_id = ?",
                (ts, session_id),
            )
            conn.commit()

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load session by id."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["participants"] = json.loads(d.get("participants") or "[]")
            d["metadata"] = json.loads(d.get("metadata") or "{}")
            d["is_active"] = bool(d.get("is_active", 1))
            return d

    def get_messages(
        self, session_id: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Load messages for session."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            q = "SELECT role, content, timestamp, metadata FROM session_messages WHERE session_id = ? ORDER BY timestamp ASC"
            params: tuple = (session_id,)
            if limit:
                q += " LIMIT ?"
                params = (session_id, limit)
            rows = conn.execute(q, params).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["metadata"] = json.loads(d.get("metadata") or "{}")
                out.append(d)
            return out

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all sessions."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT session_id, user_id, session_type, participants, "
                "created_at, last_activity, metadata, is_active FROM sessions"
            ).fetchall()
            out = []
            for row in rows:
                d = dict(row)
                d["participants"] = json.loads(d.get("participants") or "[]")
                d["metadata"] = json.loads(d.get("metadata") or "{}")
                d["is_active"] = bool(d.get("is_active", 1))
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM session_messages WHERE session_id = ?",
                    (d["session_id"],),
                ).fetchone()[0]
                d["message_count"] = cnt
                out.append(d)
            return out

    def delete_session(self, session_id: str) -> bool:
        """Delete session and its messages."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM session_messages WHERE session_id = ?", (session_id,))
            cur = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()
            return cur.rowcount > 0

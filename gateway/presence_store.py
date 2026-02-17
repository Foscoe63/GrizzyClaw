"""Presence persistence: last_seen across restarts"""

import sqlite3
import time
from pathlib import Path
from typing import Dict

PRESENCE_DB = Path.home() / ".grizzyclaw" / "presence.db"


def _init_db():
    PRESENCE_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(PRESENCE_DB)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS presence (
                user_id TEXT PRIMARY KEY,
                last_seen REAL NOT NULL
            )
        """)
        conn.commit()


def save_presence(user_id: str, last_seen: float) -> None:
    """Upsert user last_seen."""
    _init_db()
    with sqlite3.connect(str(PRESENCE_DB)) as conn:
        conn.execute(
            "INSERT INTO presence (user_id, last_seen) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_seen = excluded.last_seen",
            (user_id, last_seen),
        )
        conn.commit()


def load_presence() -> Dict[str, float]:
    """Load all presence (user_id -> last_seen)."""
    _init_db()
    with sqlite3.connect(str(PRESENCE_DB)) as conn:
        rows = conn.execute(
            "SELECT user_id, last_seen FROM presence"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

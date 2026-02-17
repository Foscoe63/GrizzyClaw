"""Media lifecycle management: storage, retention, pruning"""

import logging
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MEDIA_BASE = Path.home() / ".grizzyclaw" / "media"
AUDIO_DIR = MEDIA_BASE / "audio"
VIDEO_DIR = MEDIA_BASE / "video"
IMAGES_DIR = MEDIA_BASE / "images"


def _get_db_path() -> Path:
    return Path.home() / ".grizzyclaw" / "grizzyclaw.db"


def _init_media_db():
    """Create media_assets table if not exists."""
    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS media_assets (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                type TEXT NOT NULL,
                user_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                ttl_seconds INTEGER
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_media_created ON media_assets(created_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_media_user ON media_assets(user_id)
        """)
        conn.commit()


def store_media(
    file_path: str,
    media_type: str,
    user_id: str,
    ttl_seconds: Optional[int] = None,
) -> str:
    """
    Record a media asset in the database. Does not move/copy the file.

    Args:
        file_path: Absolute path to the file
        media_type: "audio", "video", or "image"
        user_id: User identifier
        ttl_seconds: Optional time-to-live in seconds

    Returns:
        Asset ID (UUID)
    """
    _init_media_db()
    asset_id = str(uuid.uuid4())
    import time
    created_at = time.time()
    db_path = _get_db_path()
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """
            INSERT INTO media_assets (id, path, type, user_id, created_at, ttl_seconds)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (asset_id, str(Path(file_path).resolve()), media_type, user_id, created_at, ttl_seconds),
        )
        conn.commit()
    return asset_id


def save_media_to_storage(
    source_path: str,
    media_type: str,
    user_id: str,
    ttl_seconds: Optional[int] = None,
) -> Optional[str]:
    """
    Copy media to ~/.grizzyclaw/media/{type}/ and record in DB.

    Args:
        source_path: Path to source file
        media_type: "audio", "video", or "image"
        user_id: User identifier
        ttl_seconds: Optional TTL

    Returns:
        New file path in media dir, or None on failure
    """
    MEDIA_BASE.mkdir(parents=True, exist_ok=True)
    if media_type == "audio":
        dest_dir = AUDIO_DIR
    elif media_type == "video":
        dest_dir = VIDEO_DIR
    else:
        dest_dir = IMAGES_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(source_path).suffix or ".bin"
    dest_path = dest_dir / f"{uuid.uuid4().hex}{ext}"
    try:
        import shutil
        shutil.copy2(source_path, dest_path)
        store_media(str(dest_path), media_type, user_id, ttl_seconds)
        return str(dest_path)
    except Exception as e:
        logger.error(f"Failed to save media: {e}", exc_info=True)
        if dest_path.exists():
            dest_path.unlink()
        return None


def prune_media(
    retention_days: int = 7,
    max_size_mb: int = 0,
) -> int:
    """
    Remove media assets older than retention_days.
    If max_size_mb > 0, also delete oldest until under cap.

    Args:
        retention_days: Delete assets older than this many days
        max_size_mb: Max total size in MB (0 = no limit); delete oldest when over

    Returns:
        Number of assets pruned
    """
    import time
    _init_media_db()
    cutoff = time.time() - (retention_days * 86400)
    db_path = _get_db_path()
    pruned = 0
    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.execute(
            "SELECT id, path FROM media_assets WHERE created_at < ?",
            (cutoff,),
        )
        rows = cursor.fetchall()
        for asset_id, path in rows:
            try:
                p = Path(path)
                if p.exists():
                    p.unlink()
                    pruned += 1
            except OSError as e:
                logger.debug(f"Could not delete {path}: {e}")
            conn.execute("DELETE FROM media_assets WHERE id = ?", (asset_id,))
        conn.commit()

        # Enforce max size: delete oldest until under cap
        if max_size_mb > 0:
            max_bytes = max_size_mb * 1024 * 1024
            cursor = conn.execute(
                "SELECT id, path FROM media_assets ORDER BY created_at ASC"
            )
            rows = cursor.fetchall()
            total = 0
            for asset_id, path in rows:
                try:
                    p = Path(path)
                    if p.exists():
                        total += p.stat().st_size
                except OSError:
                    pass
            for asset_id, path in rows:
                if total <= max_bytes:
                    break
                try:
                    p = Path(path)
                    if p.exists():
                        size = p.stat().st_size
                        p.unlink()
                        total -= size
                        pruned += 1
                except OSError as e:
                    logger.debug(f"Could not delete {path}: {e}")
                conn.execute("DELETE FROM media_assets WHERE id = ?", (asset_id,))
            conn.commit()

    if pruned:
        logger.info(f"Pruned {pruned} media assets")
    return pruned

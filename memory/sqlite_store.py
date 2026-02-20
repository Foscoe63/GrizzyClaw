import json
import logging
import os
import sqlite3
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import MemoryStore, MemoryItem, MemoryCategory

logger = logging.getLogger(__name__)

# Embedding dimension (all-MiniLM-L6-v2)
EMBEDDING_DIM = 384


def _serialize_f32(vec: List[float]) -> bytes:
    """Serialize float list to binary for sqlite-vec."""
    return struct.pack(f"{len(vec)}f", *vec)


class SQLiteMemoryStore(MemoryStore):
    def __init__(
        self,
        db_path: str = "grizzyclaw.db",
        openai_api_key: Optional[str] = None,
        use_semantic: bool = True,
    ):
        # Use absolute path in user's home directory for app data
        # Special case: :memory: for in-memory DB (e.g. tests)
        if db_path != ":memory:" and not os.path.isabs(db_path):
            app_data_dir = Path.home() / ".grizzyclaw"
            app_data_dir.mkdir(exist_ok=True)
            db_path = str(app_data_dir / db_path)
        self.db_path = db_path
        self.openai_api_key = openai_api_key
        self.use_semantic = use_semantic
        self._vec_available = False
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_items (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    category TEXT,
                    source TEXT,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_categories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_user ON memory_items(user_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_category ON memory_items(category)
            """)
            conn.commit()

        # Optional: sqlite-vec for semantic search
        if self.use_semantic:
            try:
                import sqlite_vec

                conn = sqlite3.connect(self.db_path)
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                conn.enable_load_extension(False)
                conn.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS vec_memory USING vec0(
                        user_id TEXT PARTITION KEY,
                        embedding float[384],
                        +memory_id TEXT
                    )
                """)
                conn.commit()
                conn.close()
                self._vec_available = True
                logger.info("Semantic memory (sqlite-vec) enabled")
            except Exception as e:
                logger.debug(f"sqlite-vec not available, using keyword search: {e}")
                self._vec_available = False

    async def add(
        self,
        user_id: str,
        content: str,
        category: Optional[str] = None,
        source: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryItem:
        item_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_items (id, user_id, content, category, source, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    user_id,
                    content,
                    category,
                    source,
                    json.dumps(metadata) if metadata else None,
                    now,
                    now,
                ),
            )
            rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()

            # Add embedding for semantic search
            if self._vec_available:
                try:
                    from .embeddings import embed_text

                    embedding = await embed_text(content[:8000], self.openai_api_key)
                    if embedding and len(embedding) == EMBEDDING_DIM:
                        import sqlite_vec

                        conn.enable_load_extension(True)
                        sqlite_vec.load(conn)
                        conn.enable_load_extension(False)
                        conn.execute(
                            """
                            INSERT INTO vec_memory(rowid, user_id, embedding, memory_id)
                            VALUES (?, ?, ?, ?)
                            """,
                            (rowid, user_id, _serialize_f32(embedding), item_id),
                        )
                        conn.commit()
                except Exception as e:
                    logger.debug(f"Failed to add embedding: {e}")

        return MemoryItem(
            id=item_id,
            user_id=user_id,
            content=content,
            category=category,
            source=source,
            metadata=metadata,
            created_at=now,
            updated_at=now,
        )

    async def retrieve(
        self, user_id: str, query: str, limit: int = 10, category: Optional[str] = None
    ) -> List[MemoryItem]:
        if category:
            return await self._retrieve_by_category(user_id, category, limit)

        # Semantic search when query non-empty and vec available
        if (
            query.strip()
            and self._vec_available
            and self.use_semantic
        ):
            try:
                return await self._retrieve_semantic(user_id, query, limit)
            except Exception as e:
                logger.debug(f"Semantic retrieve failed, falling back to keyword: {e}")

        return await self._retrieve_keyword(user_id, query, limit)

    async def _retrieve_by_category(
        self, user_id: str, category: str, limit: int
    ) -> List[MemoryItem]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM memory_items
                WHERE user_id = ? AND category = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, category, limit),
            ).fetchall()
        return self._rows_to_items(rows)

    async def _retrieve_semantic(
        self, user_id: str, query: str, limit: int
    ) -> List[MemoryItem]:
        from .embeddings import embed_text

        embedding = await embed_text(query[:8000], self.openai_api_key)
        if not embedding or len(embedding) != EMBEDDING_DIM:
            return await self._retrieve_keyword(user_id, query, limit)

        import sqlite_vec

        with sqlite3.connect(self.db_path) as conn:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            conn.row_factory = sqlite3.Row

            # KNN search with partition filter
            rows = conn.execute(
                """
                SELECT v.memory_id, v.distance
                FROM vec_memory v
                WHERE v.user_id = ? AND v.embedding MATCH ? AND k = ?
                ORDER BY v.distance
                """,
                (user_id, _serialize_f32(embedding), limit),
            ).fetchall()

            if not rows:
                # No vectors yet (e.g. pre-existing memories) - fall back to keyword
                return await self._retrieve_keyword(user_id, query, limit)

            memory_ids = [r["memory_id"] for r in rows]
            placeholders = ",".join("?" * len(memory_ids))
            items_rows = conn.execute(
                f"""
                SELECT * FROM memory_items
                WHERE id IN ({placeholders})
                """,
                memory_ids,
            ).fetchall()

            # Preserve order by distance
            id_to_row = {r["id"]: r for r in items_rows}
            ordered = [id_to_row[mid] for mid in memory_ids if mid in id_to_row]

        return self._rows_to_items(ordered)

    async def _retrieve_keyword(
        self, user_id: str, query: str, limit: int
    ) -> List[MemoryItem]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if query.strip():
                # Escape LIKE special chars (% _ \) to prevent unintended wildcard matching
                escaped = (
                    query.replace("\\", "\\\\")
                    .replace("%", "\\%")
                    .replace("_", "\\_")
                )
                pattern = f"%{escaped}%"
                rows = conn.execute(
                    """
                    SELECT * FROM memory_items
                    WHERE user_id = ? AND content LIKE ? ESCAPE '\\'
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (user_id, pattern, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM memory_items
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
        return self._rows_to_items(rows)

    def _rows_to_items(self, rows: List[sqlite3.Row]) -> List[MemoryItem]:
        return [
            MemoryItem(
                id=row["id"],
                user_id=row["user_id"],
                content=row["content"],
                category=row["category"],
                source=row["source"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else None,
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]

    async def get_categories(self, user_id: str) -> List[MemoryCategory]:
        """Derive categories from memory_items (category column)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            rows = conn.execute(
                """
                SELECT COALESCE(category, 'general') as category, COUNT(*) as item_count
                FROM memory_items
                WHERE user_id = ?
                GROUP BY category
                ORDER BY item_count DESC
                """,
                (user_id,),
            ).fetchall()

        return [
            MemoryCategory(
                id=row["category"],
                name=row["category"],
                description=None,
                item_count=row["item_count"],
            )
            for row in rows
        ]

    async def delete(self, item_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            if self._vec_available:
                try:
                    row = conn.execute(
                        "SELECT rowid FROM memory_items WHERE id = ?", (item_id,)
                    ).fetchone()
                    if row:
                        conn.execute(
                            "DELETE FROM vec_memory WHERE rowid = ?", (row[0],)
                        )
                except Exception as e:
                    logger.debug(f"Failed to delete vec row: {e}")
            cursor = conn.execute("DELETE FROM memory_items WHERE id = ?", (item_id,))
            conn.commit()
            return cursor.rowcount > 0

    async def get_user_memory(self, user_id: str) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            total_items = conn.execute(
                "SELECT COUNT(*) as count FROM memory_items WHERE user_id = ?",
                (user_id,),
            ).fetchone()["count"]

            categories = await self.get_categories(user_id)

            recent_items = await self.retrieve(user_id, "", limit=5)

        return {
            "total_items": total_items,
            "categories": [
                {"id": c.id, "name": c.name, "item_count": c.item_count}
                for c in categories
            ],
            "recent_items": [
                {"id": i.id, "content": i.content[:100], "created_at": i.created_at}
                for i in recent_items
            ],
        }

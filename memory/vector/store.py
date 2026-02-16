"""Vector store using PostgreSQL + pgvector for semantic memory

This implements memU-style proactive memory with three-layer hierarchy:
- Resources: Original data sources (conversations, documents)
- Items: Extracted facts and preferences
- Categories: Auto-organized topics
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import asyncpg
import numpy as np

logger = logging.getLogger(__name__)


class VectorStore:
    """PostgreSQL + pgvector store for semantic memory"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        database: str = "grizzyclaw",
        user: str = "grizzyclaw",
        password: str = "grizzyclaw"
    ):
        """Initialize vector store

        Args:
            host: PostgreSQL host
            port: PostgreSQL port
            database: Database name
            user: Database user
            password: Database password
        """
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        """Connect to PostgreSQL and setup pgvector"""
        logger.info(f"Connecting to PostgreSQL at {self.host}:{self.port}")

        try:
            # Create connection pool
            self.pool = await asyncpg.create_pool(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                min_size=2,
                max_size=10
            )

            # Initialize schema
            await self._init_schema()

            logger.info("✓ Connected to PostgreSQL with pgvector")

        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL: {e}", exc_info=True)
            raise

    async def disconnect(self):
        """Disconnect from PostgreSQL"""
        if self.pool:
            await self.pool.close()
            logger.info("✓ Disconnected from PostgreSQL")

    async def _init_schema(self):
        """Initialize database schema with pgvector"""
        async with self.pool.acquire() as conn:
            # Enable pgvector extension
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")

            # Create resources table (original data)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS resources (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata JSONB DEFAULT '{}',
                    embedding vector(1536),
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # Create items table (extracted facts/preferences)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    resource_id INTEGER REFERENCES resources(id),
                    item_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance FLOAT DEFAULT 0.5,
                    metadata JSONB DEFAULT '{}',
                    embedding vector(1536),
                    created_at TIMESTAMP DEFAULT NOW(),
                    accessed_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # Create categories table (auto-organized topics)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    embedding vector(1536),
                    item_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, name)
                )
            """)

            # Create item_categories junction table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS item_categories (
                    item_id INTEGER REFERENCES items(id) ON DELETE CASCADE,
                    category_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
                    confidence FLOAT DEFAULT 1.0,
                    PRIMARY KEY (item_id, category_id)
                )
            """)

            # Create indexes for performance
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_resources_user
                ON resources(user_id)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_items_user
                ON items(user_id)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_categories_user
                ON categories(user_id)
            """)

            # Create vector similarity indexes
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_resources_embedding
                ON resources USING ivfflat (embedding vector_cosine_ops)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_items_embedding
                ON items USING ivfflat (embedding vector_cosine_ops)
            """)

            logger.info("✓ Database schema initialized")

    async def add_resource(
        self,
        user_id: str,
        resource_type: str,
        content: str,
        embedding: List[float],
        metadata: Optional[Dict] = None
    ) -> int:
        """Add a resource (original data source)

        Args:
            user_id: User identifier
            resource_type: Type of resource (conversation, document, etc.)
            content: Resource content
            embedding: Vector embedding
            metadata: Additional metadata

        Returns:
            Resource ID
        """
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                """
                INSERT INTO resources (user_id, resource_type, content, embedding, metadata)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                user_id,
                resource_type,
                content,
                np.array(embedding),
                metadata or {}
            )
            return result['id']

    async def add_item(
        self,
        user_id: str,
        item_type: str,
        content: str,
        embedding: List[float],
        resource_id: Optional[int] = None,
        importance: float = 0.5,
        metadata: Optional[Dict] = None
    ) -> int:
        """Add an item (extracted fact/preference)

        Args:
            user_id: User identifier
            item_type: Type of item (fact, preference, etc.)
            content: Item content
            embedding: Vector embedding
            resource_id: Source resource ID
            importance: Importance score (0-1)
            metadata: Additional metadata

        Returns:
            Item ID
        """
        async with self.pool.acquire() as conn:
            result = await conn.fetchrow(
                """
                INSERT INTO items (user_id, resource_id, item_type, content,
                                 importance, embedding, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
                """,
                user_id,
                resource_id,
                item_type,
                content,
                importance,
                np.array(embedding),
                metadata or {}
            )
            return result['id']

    async def semantic_search(
        self,
        user_id: str,
        query_embedding: List[float],
        limit: int = 10,
        search_type: str = "items"
    ) -> List[Dict[str, Any]]:
        """Semantic search using vector similarity

        Args:
            user_id: User identifier
            query_embedding: Query vector
            limit: Maximum results
            search_type: 'items' or 'resources'

        Returns:
            List of matching results with similarity scores
        """
        table = "items" if search_type == "items" else "resources"

        async with self.pool.acquire() as conn:
            results = await conn.fetch(
                f"""
                SELECT id, content, metadata,
                       1 - (embedding <=> $1) as similarity
                FROM {table}
                WHERE user_id = $2
                ORDER BY embedding <=> $1
                LIMIT $3
                """,
                np.array(query_embedding),
                user_id,
                limit
            )

            return [
                {
                    "id": row['id'],
                    "content": row['content'],
                    "metadata": row['metadata'],
                    "similarity": float(row['similarity'])
                }
                for row in results
            ]

    async def get_user_memory(
        self,
        user_id: str,
        limit: int = 100
    ) -> Dict[str, Any]:
        """Get user's memory summary

        Args:
            user_id: User identifier
            limit: Maximum items to retrieve

        Returns:
            Memory summary
        """
        async with self.pool.acquire() as conn:
            # Get total counts
            resource_count = await conn.fetchval(
                "SELECT COUNT(*) FROM resources WHERE user_id = $1",
                user_id
            )

            item_count = await conn.fetchval(
                "SELECT COUNT(*) FROM items WHERE user_id = $1",
                user_id
            )

            category_count = await conn.fetchval(
                "SELECT COUNT(*) FROM categories WHERE user_id = $1",
                user_id
            )

            # Get recent items
            recent_items = await conn.fetch(
                """
                SELECT id, content, item_type, importance, created_at
                FROM items
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                user_id,
                limit
            )

            return {
                "total_resources": resource_count,
                "total_items": item_count,
                "total_categories": category_count,
                "recent_items": [
                    {
                        "id": row['id'],
                        "content": row['content'],
                        "type": row['item_type'],
                        "importance": float(row['importance']),
                        "created_at": row['created_at'].isoformat()
                    }
                    for row in recent_items
                ]
            }

    async def create_category(
        self,
        user_id: str,
        name: str,
        description: str,
        embedding: List[float]
    ) -> int:
        """Create a category

        Args:
            user_id: User identifier
            name: Category name
            description: Category description
            embedding: Vector embedding

        Returns:
            Category ID
        """
        async with self.pool.acquire() as conn:
            try:
                result = await conn.fetchrow(
                    """
                    INSERT INTO categories (user_id, name, description, embedding)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id
                    """,
                    user_id,
                    name,
                    description,
                    np.array(embedding)
                )
                return result['id']
            except asyncpg.UniqueViolationError:
                # Category already exists, return existing
                result = await conn.fetchrow(
                    """
                    SELECT id FROM categories
                    WHERE user_id = $1 AND name = $2
                    """,
                    user_id,
                    name
                )
                return result['id']

    async def link_item_to_category(
        self,
        item_id: int,
        category_id: int,
        confidence: float = 1.0
    ):
        """Link an item to a category

        Args:
            item_id: Item ID
            category_id: Category ID
            confidence: Confidence score (0-1)
        """
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO item_categories (item_id, category_id, confidence)
                VALUES ($1, $2, $3)
                ON CONFLICT (item_id, category_id)
                DO UPDATE SET confidence = $3
                """,
                item_id,
                category_id,
                confidence
            )

            # Update category item count
            await conn.execute(
                """
                UPDATE categories
                SET item_count = (
                    SELECT COUNT(*) FROM item_categories
                    WHERE category_id = $1
                )
                WHERE id = $1
                """,
                category_id
            )

    async def delete_item(self, item_id: int) -> bool:
        """Delete an item by ID (cascades to item_categories).

        Args:
            item_id: Item ID to delete

        Returns:
            True if deleted
        """
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM items WHERE id = $1",
                item_id
            )
            return result == "DELETE 1"

    async def list_items_for_compaction(
        self,
        user_id: str,
        limit: int = 10000
    ) -> List[Dict[str, Any]]:
        """List items ordered by priority for compaction (lowest first).
        Keeps high-importance and recently-accessed items.

        Args:
            user_id: User identifier
            limit: Maximum items to return

        Returns:
            List of items with id, importance, accessed_at
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, importance, accessed_at
                FROM items
                WHERE user_id = $1
                ORDER BY importance ASC, accessed_at ASC NULLS FIRST
                LIMIT $2
                """,
                user_id,
                limit
            )
            return [
                {"id": row["id"], "importance": float(row["importance"]), "accessed_at": row["accessed_at"]}
                for row in rows
            ]

"""Proactive memory manager with embedding generation"""

import logging
from typing import List, Dict, Any, Optional
import asyncio

from .store import VectorStore

logger = logging.getLogger(__name__)


class ProactiveMemory:
    """Proactive memory system with semantic search

    Implements memU-style three-layer memory:
    - Resources: Conversations, documents
    - Items: Extracted facts, preferences
    - Categories: Auto-organized topics
    """

    def __init__(self, vector_store: VectorStore, embedding_provider: Optional[Any] = None):
        """Initialize proactive memory

        Args:
            vector_store: Vector store instance
            embedding_provider: Provider for generating embeddings (OpenAI, etc.)
        """
        self.store = vector_store
        self.embedding_provider = embedding_provider

    async def generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for text

        Args:
            text: Text to embed

        Returns:
            Embedding vector
        """
        if self.embedding_provider:
            try:
                # Use configured embedding provider
                return await self.embedding_provider.embed(text)
            except Exception as e:
                logger.error(f"Failed to generate embedding: {e}")

        # Fallback: simple hash-based embedding (for testing)
        # In production, use OpenAI embeddings or similar
        import hashlib
        hash_obj = hashlib.sha256(text.encode())
        hash_bytes = hash_obj.digest()

        # Convert to 1536-dim vector (OpenAI embedding size)
        embedding = []
        for i in range(1536):
            byte_idx = i % len(hash_bytes)
            embedding.append(float(hash_bytes[byte_idx]) / 255.0)

        return embedding

    async def store_conversation(
        self,
        user_id: str,
        messages: List[Dict[str, str]],
        metadata: Optional[Dict] = None
    ) -> int:
        """Store a conversation as a resource

        Args:
            user_id: User identifier
            messages: List of message dicts with 'role' and 'content'
            metadata: Additional metadata

        Returns:
            Resource ID
        """
        # Combine messages into single text
        conversation_text = "\n".join([
            f"{msg['role']}: {msg['content']}"
            for msg in messages
        ])

        # Generate embedding
        embedding = await self.generate_embedding(conversation_text)

        # Store as resource
        resource_id = await self.store.add_resource(
            user_id=user_id,
            resource_type="conversation",
            content=conversation_text,
            embedding=embedding,
            metadata=metadata or {}
        )

        logger.info(f"Stored conversation for user {user_id} (resource {resource_id})")

        # Extract items asynchronously
        asyncio.create_task(self._extract_items(user_id, resource_id, conversation_text))

        return resource_id

    async def _extract_items(
        self,
        user_id: str,
        resource_id: int,
        content: str
    ):
        """Extract items (facts, preferences) from content

        Args:
            user_id: User identifier
            resource_id: Source resource ID
            content: Content to extract from
        """
        try:
            # Simple extraction: split into sentences
            # In production, use LLM to extract facts/preferences
            sentences = [s.strip() for s in content.split('.') if s.strip()]

            for sentence in sentences[:10]:  # Limit to 10 items per conversation
                if len(sentence) > 20:  # Minimum length
                    embedding = await self.generate_embedding(sentence)

                    # Determine item type and importance
                    item_type = "fact"
                    importance = 0.5

                    if any(word in sentence.lower() for word in ['prefer', 'like', 'love', 'hate']):
                        item_type = "preference"
                        importance = 0.8

                    await self.store.add_item(
                        user_id=user_id,
                        item_type=item_type,
                        content=sentence,
                        embedding=embedding,
                        resource_id=resource_id,
                        importance=importance
                    )

            logger.info(f"Extracted items from resource {resource_id}")

        except Exception as e:
            logger.error(f"Failed to extract items: {e}", exc_info=True)

    async def search_memory(
        self,
        user_id: str,
        query: str,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Search user's memory semantically

        Args:
            user_id: User identifier
            query: Search query
            limit: Maximum results

        Returns:
            List of matching memory items
        """
        # Generate query embedding
        query_embedding = await self.generate_embedding(query)

        # Search items
        results = await self.store.semantic_search(
            user_id=user_id,
            query_embedding=query_embedding,
            limit=limit,
            search_type="items"
        )

        logger.info(f"Memory search for '{query[:50]}...' returned {len(results)} results")

        return results

    async def get_context(
        self,
        user_id: str,
        current_message: str,
        max_items: int = 5
    ) -> List[str]:
        """Get relevant context from memory for current message

        Args:
            user_id: User identifier
            current_message: Current message text
            max_items: Maximum context items

        Returns:
            List of relevant memory items
        """
        results = await self.search_memory(user_id, current_message, limit=max_items)

        # Filter by similarity threshold
        relevant = [
            r['content']
            for r in results
            if r['similarity'] > 0.7  # Only high-similarity items
        ]

        return relevant

    async def auto_categorize(
        self,
        user_id: str,
        item_id: int,
        item_text: str
    ):
        """Automatically categorize an item

        Args:
            user_id: User identifier
            item_id: Item ID
            item_text: Item content
        """
        # Simple keyword-based categorization
        # In production, use LLM or clustering

        categories = {
            "preferences": ["prefer", "like", "love", "favorite", "enjoy"],
            "work": ["work", "job", "career", "project", "meeting"],
            "personal": ["family", "friend", "home", "personal"],
            "hobbies": ["hobby", "interest", "passion", "fun"]
        }

        item_lower = item_text.lower()

        for category_name, keywords in categories.items():
            if any(keyword in item_lower for keyword in keywords):
                # Create or get category
                embedding = await self.generate_embedding(category_name)

                category_id = await self.store.create_category(
                    user_id=user_id,
                    name=category_name,
                    description=f"Items related to {category_name}",
                    embedding=embedding
                )

                # Link item to category
                await self.store.link_item_to_category(
                    item_id=item_id,
                    category_id=category_id,
                    confidence=0.8
                )

                logger.debug(f"Categorized item {item_id} as {category_name}")

    async def get_user_stats(self, user_id: str) -> Dict[str, Any]:
        """Get memory statistics for user

        Args:
            user_id: User identifier

        Returns:
            Statistics dictionary
        """
        return await self.store.get_user_memory(user_id)

    async def compact_memory(self, user_id: str, max_items: int = 1000):
        """Compact user's memory by removing low-importance items.

        Keeps the most important and recently-accessed items. Removes items
        with lowest importance and oldest access first until under max_items.

        Args:
            user_id: User identifier
            max_items: Maximum items to keep
        """
        stats = await self.store.get_user_memory(user_id)
        total = stats.get("total_items", 0)
        if total <= max_items:
            logger.debug(f"Memory for {user_id} already under limit ({total} <= {max_items})")
            return

        to_remove = total - max_items
        candidates = await self.store.list_items_for_compaction(user_id, limit=to_remove + 100)
        deleted = 0
        for item in candidates[:to_remove]:
            if await self.store.delete_item(item["id"]):
                deleted += 1
            if deleted >= to_remove:
                break

        logger.info(f"Memory compaction for user {user_id}: removed {deleted} items (kept {total - deleted})")

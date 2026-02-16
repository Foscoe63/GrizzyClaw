"""Embedding providers for semantic memory. Tries sentence-transformers, OpenAI, then hash fallback."""
from __future__ import annotations

import hashlib
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 384  # all-MiniLM-L6-v2; OpenAI uses 1536 but we standardize on 384 for sqlite-vec

_sentence_transformer = None


def _get_sentence_transformer():
    """Lazy-load sentence-transformers (optional dependency)."""
    global _sentence_transformer
    if _sentence_transformer is not None:
        return _sentence_transformer
    try:
        from sentence_transformers import SentenceTransformer

        _sentence_transformer = SentenceTransformer("all-MiniLM-L6-v2")
        return _sentence_transformer
    except ImportError:
        return None


async def embed_text(text: str, openai_api_key: Optional[str] = None) -> Optional[List[float]]:
    """
    Generate embedding for text. Tries: sentence-transformers -> OpenAI -> hash fallback.
    Returns None if all fail. Hash fallback produces low-quality but deterministic vectors.
    """
    # 1. Try sentence-transformers (local, no API)
    model = _get_sentence_transformer()
    if model is not None:
        try:
            # SentenceTransformer.encode is sync; run in executor to avoid blocking
            import asyncio

            loop = asyncio.get_event_loop()
            vec = await loop.run_in_executor(None, lambda: model.encode(text, convert_to_numpy=True))
            return [float(x) for x in vec.tolist()]
        except Exception as e:
            logger.debug(f"Sentence-transformer embed failed: {e}")

    # 2. Try OpenAI embeddings (when API key available)
    if openai_api_key:
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {openai_api_key}",
                    },
                    json={"model": "text-embedding-3-small", "input": text[:8000]},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        vec = data["data"][0]["embedding"]
                        # OpenAI text-embedding-3-small is 1536-dim; truncate/pad to 384 for compatibility
                        if len(vec) >= EMBEDDING_DIM:
                            return [float(x) for x in vec[:EMBEDDING_DIM]]
                        return [float(x) for x in vec] + [0.0] * (EMBEDDING_DIM - len(vec))
        except Exception as e:
            logger.debug(f"OpenAI embed failed: {e}")

    # 3. Hash fallback (deterministic but poor semantic quality)
    try:
        h = hashlib.sha256(text.encode()).digest()
        return [float((h[i % len(h)] ^ h[(i + 1) % len(h)]) / 255.0) for i in range(EMBEDDING_DIM)]
    except Exception as e:
        logger.warning(f"Hash embed failed: {e}")
        return None

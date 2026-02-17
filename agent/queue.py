"""Message queue for agent: per-session serialization to avoid context bleed"""

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, Optional

logger = logging.getLogger(__name__)


class AgentQueue:
    """
    Per-session lock to serialize message processing.
    When agent is busy on a session, subsequent messages wait.
    queue_max_per_session limits how many can be queued (waiting + processing).
    """

    def __init__(self, agent: Any, max_per_session: int = 50):
        self._agent = agent
        self._locks: Dict[str, asyncio.Lock] = {}
        self._semaphores: Dict[str, asyncio.Semaphore] = {}
        self._max_per_session = max_per_session

    def _get_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    def _get_semaphore(self, session_id: str) -> asyncio.Semaphore:
        if session_id not in self._semaphores:
            self._semaphores[session_id] = asyncio.Semaphore(self._max_per_session)
        return self._semaphores[session_id]

    async def process_message(
        self,
        session_id: str,
        user_id: str,
        message: str,
        **kwargs,
    ) -> AsyncIterator[str]:
        """
        Process message with session lock. Yields chunks from agent.
        Rejects if queue (waiting + processing) is at max_per_session.
        """
        sem = self._get_semaphore(session_id)
        try:
            await asyncio.wait_for(sem.acquire(), timeout=0)
        except asyncio.TimeoutError:
            logger.warning(f"Session {session_id}: queue full (max={self._max_per_session})")
            yield "⚠️ Queue full. Please try again in a moment."
            return
        try:
            lock = self._get_lock(session_id)
            async with lock:
                async for chunk in self._agent.process_message(
                    user_id, message, **kwargs
                ):
                    yield chunk
        finally:
            sem.release()

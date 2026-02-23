"""Message queue for agent: per-session serialization to avoid context bleed"""

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, Optional

logger = logging.getLogger(__name__)

# Default per-message timeout so a stuck request does not hold the queue indefinitely
DEFAULT_PROCESS_MESSAGE_TIMEOUT = 300  # 5 minutes


class AgentQueue:
    """
    Per-session lock to serialize message processing.
    When agent is busy on a session, subsequent messages wait.
    queue_max_per_session limits how many can be queued (waiting + processing).
    Per-message timeout ensures a stuck request does not hold the queue indefinitely.
    """

    def __init__(
        self,
        agent: Any,
        max_per_session: int = 50,
        process_message_timeout: float = DEFAULT_PROCESS_MESSAGE_TIMEOUT,
    ):
        self._agent = agent
        self._locks: Dict[str, asyncio.Lock] = {}
        self._semaphores: Dict[str, asyncio.Semaphore] = {}
        self._max_per_session = max_per_session
        self._process_message_timeout = process_message_timeout

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
                # Stream with global timeout: collect chunks in a task, yield with deadline
                result_queue: asyncio.Queue = asyncio.Queue()

                async def collect() -> None:
                    try:
                        async for ch in self._agent.process_message(
                            user_id, message, **kwargs
                        ):
                            await result_queue.put(ch)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.exception("Agent process_message error: %s", e)
                        await result_queue.put(f"⚠️ Error: {e}")
                    finally:
                        await result_queue.put(None)

                task = asyncio.create_task(collect())
                deadline = asyncio.get_event_loop().time() + self._process_message_timeout
                try:
                    while True:
                        remaining = max(0.01, deadline - asyncio.get_event_loop().time())
                        try:
                            chunk = await asyncio.wait_for(
                                result_queue.get(), timeout=remaining
                            )
                        except asyncio.TimeoutError:
                            task.cancel()
                            try:
                                await task
                            except asyncio.CancelledError:
                                pass
                            logger.warning(
                                "Session %s: process_message timed out after %.0fs",
                                session_id,
                                self._process_message_timeout,
                            )
                            yield "⚠️ Request timed out. Please try again or use a shorter prompt."
                            return
                        if chunk is None:
                            break
                        yield chunk
                finally:
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
        finally:
            sem.release()

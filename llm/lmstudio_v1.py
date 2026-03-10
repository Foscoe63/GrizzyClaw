"""
LM Studio native v1 REST API provider (POST /api/v1/chat).
Use when you want stateful chat, MCP via integrations, or v1 streaming events.
For OpenAI-compatible usage, use LMStudioProvider instead.
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from . import LLMProvider, LLMError, LLMProviderNotAvailable

logger = logging.getLogger(__name__)


def _normalize_v1_base_url(url: str) -> str:
    """Ensure v1 base URL has scheme and no /v1 or /api path (e.g. localhost:1234 -> http://localhost:1234)."""
    url = (url or "").strip()
    if not url:
        return "http://localhost:1234"
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    url = url.rstrip("/")
    if "/v1" in url and not url.endswith("/v1"):
        url = url.split("/v1")[0]
    elif url.endswith("/v1"):
        url = url[:-3]
    if url.endswith("/api"):
        url = url[:-4]
    return url


def _messages_to_v1_input_and_system(messages: List[Dict[str, Any]]) -> tuple[str, str, Optional[str]]:
    """
    Map OpenAI-format messages to LM Studio v1 input and system_prompt.
    Returns (input_string, system_prompt, previous_response_id).
    For stateless: uses last user message as input, concatenates system messages.
    """
    system_parts: List[str] = []
    last_user_content: Optional[str] = None
    previous_response_id: Optional[str] = None

    for msg in messages:
        role = (msg.get("role") or "").strip().lower()
        content = msg.get("content")
        if role == "system" and content:
            system_parts.append(content if isinstance(content, str) else str(content))
        elif role == "user":
            if isinstance(content, str):
                last_user_content = content
            elif isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                if text_parts:
                    last_user_content = "\n".join(text_parts)
        elif role == "assistant" and msg.get("tool_calls"):
            # Client-side tool loop: we're not using v1 MCP; ignore for input
            pass
        elif role == "tool":
            # Tool result; could append to context; for simplicity we keep last user message only
            pass
        # Optional: read previous_response_id from a stored field if we add stateful support
        if role == "user" and isinstance(msg.get("previous_response_id"), str):
            previous_response_id = msg["previous_response_id"]

    system_prompt = "\n\n".join(system_parts) if system_parts else ""
    input_str = last_user_content if last_user_content else ""
    return input_str, system_prompt, previous_response_id


class LMStudioV1Provider(LLMProvider):
    """LM Studio native v1 REST API: /api/v1/chat with streaming (message.delta) and optional stateful chat."""

    def __init__(
        self,
        base_url: str = "http://localhost:1234",
        api_key: Optional[str] = None,
    ):
        super().__init__("lmstudio_v1", _normalize_v1_base_url(base_url), api_key)

    def _chat_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1/chat"

    def _models_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1/models"

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        input_str, system_prompt, previous_response_id = _messages_to_v1_input_and_system(messages)
        if not input_str.strip():
            logger.warning("LM Studio v1: no user input after mapping messages")
            return

        payload: Dict[str, Any] = {
            "model": model or "",
            "input": input_str,
            "stream": True,
            "temperature": temperature,
            "store": False,
        }
        if system_prompt:
            payload["system_prompt"] = system_prompt
        if max_tokens:
            payload["max_output_tokens"] = max_tokens
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id

        timeout = aiohttp.ClientTimeout(total=300, connect=30)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    self._chat_url(),
                    json=payload,
                    headers=self._headers(),
                ) as response:
                    if response.status != 200:
                        body = await response.text()
                        raise LLMError(
                            f"LM Studio v1 error: {response.status}"
                            + (f" — {body[:200]}" if body else "")
                        )
                    async for line in response.content:
                        line_str = line.decode("utf-8", errors="replace").strip()
                        if not line_str.startswith("event:") and not line_str.startswith("data:"):
                            continue
                        if line_str.startswith("data:"):
                            data_str = line_str[5:].strip()
                            if not data_str:
                                continue
                            try:
                                data = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            if not isinstance(data, dict):
                                continue
                            event_type = data.get("type", "")
                            if event_type == "message.delta":
                                content = data.get("content")
                                if isinstance(content, str) and content:
                                    yield content
                            elif event_type == "error":
                                err = data.get("error") or {}
                                msg = err.get("message", "Unknown error")
                                raise LLMError(f"LM Studio v1 stream error: {msg}")
        except LLMError:
            raise
        except LLMProviderNotAvailable:
            raise
        except aiohttp.ClientError as e:
            raise LLMProviderNotAvailable(f"Cannot connect to LM Studio v1: {e}") from e
        except Exception as e:
            raise LLMError(str(e)) from e

    async def health_check(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self._models_url(), headers=self._headers()) as response:
                    return response.status == 200
        except Exception as e:
            logger.debug("LM Studio v1 health check failed: %s", e)
            return False

    async def list_models(self) -> List[Dict[str, Any]]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self._models_url(), headers=self._headers()) as response:
                    if response.status != 200:
                        return []
                    data = await response.json()
                    models = data.get("models") or []
                    out: List[Dict[str, Any]] = []
                    for m in models:
                        if not isinstance(m, dict):
                            continue
                        key = m.get("key") or m.get("id") or ""
                        if not key:
                            continue
                        if m.get("type") == "embedding":
                            continue
                        out.append({
                            "id": key,
                            "name": m.get("display_name") or m.get("key") or key,
                        })
                    return out
        except Exception as e:
            logger.debug("LM Studio v1 list_models failed: %s", e)
            return []

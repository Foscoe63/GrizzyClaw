import logging

import aiohttp
from typing import Any, AsyncIterator, Dict, List, Optional

from . import (
    LLMProvider,
    LLMError,
    LLMProviderNotAvailable,
    LLMAuthenticationError,
    LLMRateLimitError,
)

logger = logging.getLogger(__name__)


def _extract_delta_content(data: Dict[str, Any]) -> Optional[str]:
    """Return streamed content token from OpenAI chunk, if present."""
    if not isinstance(data, dict):
        return None
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    delta = first.get("delta")
    if not isinstance(delta, dict):
        return None
    content = delta.get("content")
    if isinstance(content, str):
        return content
    return None


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1"):
        super().__init__("openai", base_url, api_key)

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        model = model or "gpt-4"

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }

        if max_tokens:
            payload["max_tokens"] = max_tokens

        try:
            timeout = aiohttp.ClientTimeout(total=120)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status == 401:
                        raise LLMAuthenticationError("Invalid OpenAI API key")
                    elif response.status == 429:
                        raise LLMRateLimitError("OpenAI rate limit exceeded")
                    elif response.status != 200:
                        raise LLMError(f"OpenAI error: {response.status}")

                    async for line in response.content:
                        line = line.decode().strip()
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            import json

                            data = json.loads(data_str)
                            content = _extract_delta_content(data)
                            if content is not None:
                                yield content
        except aiohttp.ClientError as e:
            raise LLMProviderNotAvailable(f"Cannot connect to OpenAI: {e}")

    async def health_check(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                ) as response:
                    return response.status == 200
        except Exception as e:
            logger.debug("OpenAI health check failed: %s", e)
            return False

    async def list_models(self) -> List[Dict[str, Any]]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                ) as response:
                    data = await response.json()
                    return [
                        {"id": m["id"], "name": m.get("name", m["id"])}
                        for m in data.get("data", [])
                    ]
        except Exception as e:
            logger.debug("OpenAI list_models failed: %s", e)
            return []

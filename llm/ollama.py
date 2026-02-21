import logging

import aiohttp
from typing import Any, AsyncIterator, Dict, List, Optional

from . import LLMProvider, LLMError, LLMProviderNotAvailable

logger = logging.getLogger(__name__)


def _parse_data_url(data_url: str) -> Optional[str]:
    """Extract base64 from data URL."""
    if not data_url.startswith("data:") or "," not in data_url:
        return None
    try:
        return data_url.split(",", 1)[1]
    except Exception:
        return None


def _convert_messages_for_ollama(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAI-format messages to Ollama format (content string + images array)."""
    result: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if isinstance(content, list):
            text_parts: List[str] = []
            images: List[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "image_url":
                    url = block.get("image_url", {}).get("url", "")
                    b64 = _parse_data_url(url)
                    if b64:
                        images.append(b64)
            out: Dict[str, Any] = {"role": role, "content": "\n".join(text_parts) or " "}
            if images:
                out["images"] = images
            result.append(out)
        else:
            result.append({"role": role, "content": content or " "})
    return result


class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str = "http://localhost:11434"):
        super().__init__("ollama", base_url)

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        model = model or "llama3.2"

        url = f"{self.base_url}/api/chat"
        ollama_messages = _convert_messages_for_ollama(messages)
        payload = {
            "model": model,
            "messages": ollama_messages,
            "stream": True,
            "options": {
                "temperature": temperature,
            },
        }

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status != 200:
                        body = ""
                        try:
                            body = (await response.text()).strip()
                        except Exception:
                            pass
                        if response.status == 404:
                            raise LLMError(
                                f"Ollama model '{model}' not found. "
                                f"Run 'ollama pull {model}' to download it."
                            )
                        raise LLMError(f"Ollama error: {response.status}" + (f" - {body[:200]}" if body else ""))

                    async for line in response.content:
                        if line:
                            import json

                            data = json.loads(line)
                            if "message" in data and "content" in data["message"]:
                                yield data["message"]["content"]
        except aiohttp.ClientError as e:
            raise LLMProviderNotAvailable(f"Cannot connect to Ollama: {e}")

    async def health_check(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/api/tags") as response:
                    return response.status == 200
        except Exception as e:
            logger.debug("Ollama health check failed: %s", e)
            return False

    async def list_models(self) -> List[Dict[str, Any]]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/api/tags") as response:
                    data = await response.json()
                    return [
                        {"id": m["name"], "name": m["name"]}
                        for m in data.get("models", [])
                    ]
        except Exception as e:
            logger.debug("Ollama list_models failed: %s", e)
            return []

    async def get_model_context_length(self, model: str) -> Optional[int]:
        """Query model's max context length via /api/show. Returns None on failure."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/api/show", json={"model": model, "verbose": False}
                ) as response:
                    if response.status != 200:
                        return None
                    data = await response.json()
                    # model_info: {"gemma3.context_length": 131072, ...} or parameters: "num_ctx 2048"
                    mi = data.get("model_info") or {}
                    for k, v in mi.items():
                        if k.endswith(".context_length") or k == "context_length":
                            if isinstance(v, (int, float)):
                                return int(v)
                    params = (data.get("parameters") or "").strip()
                    for line in params.splitlines():
                        parts = line.split()
                        if len(parts) >= 2 and parts[0] == "num_ctx":
                            return int(parts[1])
                    return None
        except Exception as e:
            logger.debug("Ollama get_model_context_length failed: %s", e)
            return None

    async def pull_model(self, model: str) -> bool:
        url = f"{self.base_url}/api/pull"
        payload = {"name": model, "stream": False}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    return response.status == 200
        except Exception as e:
            logger.debug("Ollama pull_model failed: %s", e)
            return False

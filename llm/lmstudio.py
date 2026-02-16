import aiohttp
from typing import Any, AsyncIterator, Dict, List, Optional

from . import LLMProvider, LLMError, LLMProviderNotAvailable


def _normalize_lmstudio_url(url: str) -> str:
    """Ensure URL has a scheme so aiohttp can connect (e.g. 192.168.0.8:1234/v1 -> http://...)."""
    url = (url or "").strip()
    if not url:
        return "http://localhost:1234/v1"
    if not url.startswith(("http://", "https://")):
        return f"http://{url}"
    return url


class LMStudioProvider(LLMProvider):
    def __init__(
        self, base_url: str = "http://localhost:1234/v1", api_key: Optional[str] = None
    ):
        super().__init__("lmstudio", _normalize_lmstudio_url(base_url), api_key)

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {"messages": messages, "temperature": temperature, "stream": True}

        if model:
            payload["model"] = model
        if max_tokens:
            payload["max_tokens"] = max_tokens

        # Timeout: allow long connect/read for remote LM Studio (e.g. 192.168.x.x)
        timeout = aiohttp.ClientTimeout(total=300, connect=30)

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status != 200:
                        body = await response.text()
                        raise LLMError(
                            f"LM Studio error: {response.status}"
                            + (f" — {body[:200]}" if body else "")
                        )

                    import json
                    async for line in response.content:
                        line = line.decode().strip()
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                data = json.loads(data_str)
                                delta = data.get("choices", [{}])[0].get("delta", {})
                                if "content" in delta:
                                    yield delta["content"]
                            except (json.JSONDecodeError, KeyError, IndexError):
                                continue  # skip malformed SSE lines
        except aiohttp.ClientError as e:
            raise LLMProviderNotAvailable(f"Cannot connect to LM Studio: {e}")
        except LLMError:
            raise
        except LLMProviderNotAvailable:
            raise

    async def health_check(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/models") as response:
                    return response.status == 200
        except:
            return False

    async def list_models(self) -> List[Dict[str, Any]]:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.base_url}/models") as response:
                    data = await response.json()
                    return [
                        {"id": m["id"], "name": m.get("id", m["id"])}
                        for m in data.get("data", [])
                    ]
        except:
            return []

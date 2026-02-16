"""Anthropic Claude provider."""
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from . import (
    LLMProvider,
    LLMError,
    LLMProviderNotAvailable,
    LLMAuthenticationError,
    LLMRateLimitError,
)

try:
    from anthropic import AsyncAnthropic
    from anthropic import (
        APIStatusError,
        AuthenticationError,
        RateLimitError,
    )
except ImportError:
    AsyncAnthropic = None  # type: ignore
    APIStatusError = Exception  # type: ignore
    AuthenticationError = Exception  # type: ignore
    RateLimitError = Exception  # type: ignore


def _parse_data_url(data_url: str) -> Optional[Tuple[str, str]]:
    """Extract base64 and media_type from data URL."""
    if not data_url.startswith("data:"):
        return None
    try:
        header, rest = data_url.split(",", 1)
        media_type = "image/png"
        if ";" in header:
            mt = header.split(";")[0].replace("data:", "").strip()
            if mt:
                media_type = mt
        return (rest, media_type)
    except Exception:
        return None


def _convert_messages(messages: List[Dict[str, Any]]) -> tuple[Optional[str], List[Dict[str, Any]]]:
    """Convert OpenAI-format messages to Anthropic format.
    Returns (system_prompt, anthropic_messages).
    Supports vision: image_url blocks -> image source blocks.
    """
    system: Optional[str] = None
    anthropic_messages: List[Dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if isinstance(content, list):
            # Multipart content (text + images) - only for user/assistant
            if role == "system":
                text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                content = "\n".join(text_parts) if text_parts else ""
                system = content if system is None else f"{system}\n\n{content}"
            elif role in ("user", "assistant"):
                blocks: List[Dict[str, Any]] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        blocks.append({"type": "text", "text": block.get("text", "")})
                    elif block.get("type") == "image_url":
                        url = block.get("image_url", {}).get("url", "")
                        parsed = _parse_data_url(url)
                        if parsed:
                            b64, media_type = parsed
                            blocks.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64,
                                },
                            })
                anthropic_messages.append({"role": role, "content": blocks if blocks else ""})
        else:
            if role == "system":
                system = content if system is None else f"{system}\n\n{content}"
            elif role in ("user", "assistant"):
                anthropic_messages.append({"role": role, "content": content})

    return system, anthropic_messages


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider."""

    def __init__(self, api_key: str):
        super().__init__("anthropic", "https://api.anthropic.com", api_key)
        self._client: Optional[AsyncAnthropic] = None

    def _get_client(self) -> "AsyncAnthropic":
        if AsyncAnthropic is None:
            raise LLMProviderNotAvailable(
                "Anthropic SDK not installed. Run: pip install anthropic"
            )
        if self._client is None:
            self._client = AsyncAnthropic(api_key=self.api_key or "")
        return self._client

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        model = model or "claude-3-5-sonnet-20241022"
        max_tokens = max_tokens or 4096

        system, anthropic_messages = _convert_messages(messages)
        if not anthropic_messages:
            raise LLMError("No messages to send")

        stream_kwargs: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": anthropic_messages,
            "temperature": temperature,
        }
        if system:
            stream_kwargs["system"] = system

        try:
            client = self._get_client()
            async with client.messages.stream(**stream_kwargs) as stream:
                async for text in stream.text_stream:
                    yield text
        except AuthenticationError as e:
            raise LLMAuthenticationError("Invalid Anthropic API key") from e
        except RateLimitError as e:
            raise LLMRateLimitError("Anthropic rate limit exceeded") from e
        except APIStatusError as e:
            raise LLMError(f"Anthropic API error: {e}") from e
        except Exception as e:
            if "anthropic" in str(e).lower() or "AsyncAnthropic" in str(e):
                raise LLMProviderNotAvailable(f"Cannot connect to Anthropic: {e}") from e
            raise LLMError(str(e)) from e

    async def health_check(self) -> bool:
        try:
            client = self._get_client()
            # Minimal request to validate API key
            await client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=5,
                messages=[{"role": "user", "content": "x"}],
            )
            return True
        except Exception:
            return False

    async def list_models(self) -> List[Dict[str, Any]]:
        """Return fixed list of Anthropic models (no public list API)."""
        return [
            {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet"},
            {"id": "claude-3-5-haiku-20241022", "name": "Claude 3.5 Haiku"},
            {"id": "claude-3-opus-20240229", "name": "Claude 3 Opus"},
            {"id": "claude-3-sonnet-20240229", "name": "Claude 3 Sonnet"},
            {"id": "claude-3-haiku-20240307", "name": "Claude 3 Haiku"},
        ]

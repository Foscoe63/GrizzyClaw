import asyncio
import logging
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from . import (
    LLMProvider,
    LLMError,
    LLMProviderNotAvailable,
    LLMRateLimitError,
    LLMAuthenticationError,
)
from .ollama import OllamaProvider
from .lmstudio import LMStudioProvider
from .lmstudio_v1 import LMStudioV1Provider
from .openai import OpenAIProvider
from .anthropic import AnthropicProvider
from .openrouter import OpenRouterProvider

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3
DEFAULT_INITIAL_BACKOFF = 1.0
DEFAULT_MAX_BACKOFF = 60.0


class LLMRouter:
    def __init__(self):
        self.providers: Dict[str, LLMProvider] = {}
        self.default_provider: Optional[str] = None
        self.provider_models: Dict[str, str] = {}  # Maps provider name to default model

    def add_provider(self, name: str, provider: LLMProvider, default: bool = False):
        self.providers[name] = provider
        # Only set default when explicitly requested; otherwise the first-added
        # provider would always win (e.g. ollama over lmstudio when ollama is added first).
        if default:
            self.default_provider = name

    def configure_from_settings(self, settings):
        ollama_url = (settings.ollama_url or "").strip() or "http://localhost:11434"
        if ollama_url:
            provider = OllamaProvider(ollama_url)
            self.add_provider(
                "ollama", provider, settings.default_llm_provider == "ollama"
            )
            self.provider_models["ollama"] = settings.ollama_model

        if settings.lmstudio_url:
            provider = LMStudioProvider(settings.lmstudio_url)
            self.add_provider(
                "lmstudio", provider, settings.default_llm_provider == "lmstudio"
            )
            self.provider_models["lmstudio"] = settings.lmstudio_model

        if getattr(settings, "lmstudio_v1_enabled", False):
            v1_url = (getattr(settings, "lmstudio_v1_url", None) or "").strip()
            if not v1_url and settings.lmstudio_url:
                base = settings.lmstudio_url.rstrip("/")
                if base.endswith("/v1"):
                    v1_url = base[:-3]
                else:
                    v1_url = base.split("/v1")[0] if "/v1" in base else base
            if v1_url:
                provider_v1 = LMStudioV1Provider(
                    base_url=v1_url,
                    api_key=getattr(settings, "lmstudio_v1_api_key", None),
                )
                self.add_provider(
                    "lmstudio_v1",
                    provider_v1,
                    settings.default_llm_provider == "lmstudio_v1",
                )
                self.provider_models["lmstudio_v1"] = (
                    getattr(settings, "lmstudio_v1_model", None) or ""
                )

        if settings.openai_api_key:
            provider = OpenAIProvider(settings.openai_api_key)
            self.add_provider(
                "openai", provider, settings.default_llm_provider == "openai"
            )
            self.provider_models["openai"] = settings.openai_model

        if settings.anthropic_api_key:
            provider = AnthropicProvider(settings.anthropic_api_key)
            self.add_provider(
                "anthropic", provider, settings.default_llm_provider == "anthropic"
            )
            self.provider_models["anthropic"] = settings.anthropic_model

        if settings.openrouter_api_key:
            provider = OpenRouterProvider(settings.openrouter_api_key)
            self.add_provider(
                "openrouter", provider, settings.default_llm_provider == "openrouter"
            )
            self.provider_models["openrouter"] = settings.openrouter_model

        if settings.cursor_api_key and (settings.cursor_url or "").strip():
            base_url = (settings.cursor_url or "").strip()
            provider = OpenAIProvider(settings.cursor_api_key, base_url=base_url)
            self.add_provider(
                "cursor", provider, settings.default_llm_provider == "cursor"
            )
            self.provider_models["cursor"] = settings.cursor_model

        custom_name = (getattr(settings, "custom_provider_name", None) or "custom").strip() or "custom"
        if getattr(settings, "custom_provider_url", None):
            provider = OpenAIProvider(
                getattr(settings, "custom_provider_api_key", None) or "",
                base_url=settings.custom_provider_url.strip(),
            )
            self.add_provider(
                custom_name, provider, settings.default_llm_provider == custom_name
            )
            self.provider_models[custom_name] = getattr(settings, "custom_provider_model", "") or ""

        # Workspace override: workspace manager sets provider-specific model (ollama_model, etc.)
        # so we don't need default_model override. Provider-specific model always wins.

        # If no provider was marked default (e.g. default_llm_provider not in list), use first available
        if self.default_provider is None and self.providers:
            self.default_provider = next(iter(self.providers))

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        on_fallback: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        provider_name = provider or self.default_provider
        # Pop so we don't pass to provider.generate
        kwargs.pop("on_fallback", None)

        if not provider_name or provider_name not in self.providers:
            raise LLMError(f"Provider '{provider_name}' not available")

        llm_provider = self.providers[provider_name]

        # Use provider's configured model if no model specified
        if not model and provider_name in self.provider_models:
            model = self.provider_models[provider_name]

        last_error: Optional[Exception] = None
        backoff = DEFAULT_INITIAL_BACKOFF
        max_retries = kwargs.pop("max_retries", DEFAULT_MAX_RETRIES) or DEFAULT_MAX_RETRIES

        for attempt in range(max_retries + 1):
            try:
                import time
                t0 = time.perf_counter()
                token_count = 0
                async for chunk in llm_provider.generate(messages, model=model, **kwargs):
                    token_count += len(chunk.split())  # Approximate
                    yield chunk
                elapsed = time.perf_counter() - t0
                try:
                    from grizzyclaw.observability.metrics import get_metrics
                    get_metrics().record_llm_call(elapsed, tokens_in=0, tokens_out=token_count, error=False)
                except Exception:
                    pass
                return
            except (LLMProviderNotAvailable, LLMError) as e:
                last_error = e
                try:
                    from grizzyclaw.observability.metrics import get_metrics
                    get_metrics().record_llm_call(0, error=True)
                except Exception:
                    pass
                if isinstance(e, LLMAuthenticationError) or attempt >= max_retries:
                    break
                wait = min(backoff, DEFAULT_MAX_BACKOFF)
                logger.warning(
                    f"LLM call failed: {e}, retrying in {wait:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, DEFAULT_MAX_BACKOFF)
            except (asyncio.TimeoutError, ConnectionError, OSError) as e:
                last_error = e
                if attempt >= max_retries:
                    break
                wait = min(backoff, DEFAULT_MAX_BACKOFF)
                logger.warning(
                    f"Transient error: {e}, retrying in {wait:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, DEFAULT_MAX_BACKOFF)

        # Try fallback providers before giving up (skip for model-not-found - user should fix config)
        first_error = str(last_error or "Unknown error").strip()
        is_model_not_found = "404" in first_error or "not found" in first_error.lower()
        if not is_model_not_found:
            logger.warning(f"Provider {provider_name} failed after {max_retries + 1} attempts: {first_error}, trying fallback")
        for name, fallback in self.providers.items():
            if name != provider_name and not is_model_not_found:
                try:
                    if await fallback.health_check():
                        logger.info(f"Falling back to {name}")
                        if on_fallback:
                            try:
                                on_fallback(name)
                            except Exception:
                                pass
                        async for chunk in fallback.generate(
                            messages, model=model, **kwargs
                        ):
                            yield chunk
                        return
                except Exception:
                    continue
        raise LLMError(
            "No LLM providers available. "
            + f"{provider_name} failed: {first_error}. "
            + "If you changed the Ollama/LM Studio URL or model in Settings, click Save and restart the app (or toggle Telegram off/on) for changes to take effect."
        )

    async def health_check(self) -> Dict[str, bool]:
        results = {}
        for name, provider in self.providers.items():
            try:
                # Short timeout so slow/unreachable providers (e.g. Anthropic) don't block
                results[name] = await asyncio.wait_for(
                    provider.health_check(), timeout=5.0
                )
            except (asyncio.TimeoutError, Exception):
                results[name] = False
        return results

    async def get_model_max_context_length(
        self, provider: str, model: str
    ) -> Optional[int]:
        """Query model's max context length (Ollama/LM Studio only). Returns None if unsupported or failed."""
        if not model or provider not in self.providers:
            return None
        prov = self.providers[provider]
        if hasattr(prov, "get_model_context_length"):
            try:
                return await asyncio.wait_for(
                    prov.get_model_context_length(model), timeout=5.0
                )
            except (asyncio.TimeoutError, Exception) as e:
                logger.debug("get_model_max_context_length failed: %s", e)
        return None

    async def list_models(self, provider: Optional[str] = None) -> List[Dict[str, Any]]:
        if provider and provider in self.providers:
            return await self.providers[provider].list_models()

        all_models = []
        for name, prov in self.providers.items():
            try:
                models = await prov.list_models()
                for m in models:
                    m["provider"] = name
                all_models.extend(models)
            except Exception as e:
                logger.debug("Failed to list models for %s: %s", name, e)
        return all_models

    async def test_connections(self):
        logger.info("Testing LLM connections...")
        health = await self.health_check()
        for name, status in health.items():
            status_str = "✓" if status else "✗"
            logger.info(f"  {name}: {status_str}")

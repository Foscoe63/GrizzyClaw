import asyncio
import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from . import (
    LLMProvider,
    LLMError,
    LLMProviderNotAvailable,
    LLMRateLimitError,
    LLMAuthenticationError,
)
from .ollama import OllamaProvider
from .lmstudio import LMStudioProvider
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
        if settings.ollama_url:
            provider = OllamaProvider(settings.ollama_url)
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

        # Workspace override: use default_model for the default provider when set
        if self.default_provider and getattr(settings, "default_model", None):
            self.provider_models[self.default_provider] = settings.default_model

        # If no provider was marked default (e.g. default_llm_provider not in list), use first available
        if self.default_provider is None and self.providers:
            self.default_provider = next(iter(self.providers))

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        provider: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        provider_name = provider or self.default_provider

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

        # Try fallback providers before giving up
        first_error = str(last_error or "Unknown error").strip()
        logger.warning(f"Provider {provider_name} failed after {max_retries + 1} attempts: {first_error}, trying fallback")
        for name, fallback in self.providers.items():
            if name != provider_name:
                try:
                    if await fallback.health_check():
                        logger.info(f"Falling back to {name}")
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
            + "If you changed the LM Studio URL in Settings, click Save for chat to use it."
        )

    async def health_check(self) -> Dict[str, bool]:
        results = {}
        for name, provider in self.providers.items():
            try:
                results[name] = await provider.health_check()
            except:
                results[name] = False
        return results

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
            except:
                pass
        return all_models

    async def test_connections(self):
        logger.info("Testing LLM connections...")
        health = await self.health_check()
        for name, status in health.items():
            status_str = "✓" if status else "✗"
            logger.info(f"  {name}: {status_str}")

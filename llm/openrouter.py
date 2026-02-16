"""OpenRouter provider - OpenAI-compatible API for multiple models."""
from .openai import OpenAIProvider


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter provider. Uses OpenAI-compatible API at openrouter.ai."""

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, api_key: str):
        super().__init__(api_key=api_key, base_url=self.BASE_URL)

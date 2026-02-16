from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional


class LLMProvider(ABC):
    def __init__(self, name: str, base_url: str, api_key: Optional[str] = None):
        self.name = name
        self.base_url = base_url
        self.api_key = api_key

    @abstractmethod
    async def generate(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        pass

    @abstractmethod
    async def list_models(self) -> List[Dict[str, Any]]:
        pass


class LLMError(Exception):
    pass


class LLMProviderNotAvailable(LLMError):
    pass


class LLMRateLimitError(LLMError):
    pass


class LLMAuthenticationError(LLMError):
    pass

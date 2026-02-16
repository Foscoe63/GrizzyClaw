from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class MemoryItem:
    id: str
    user_id: str
    content: str
    category: Optional[str] = None
    source: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class MemoryCategory:
    id: str
    name: str
    description: Optional[str] = None
    item_count: int = 0


class MemoryStore(ABC):
    @abstractmethod
    async def add(
        self,
        user_id: str,
        content: str,
        category: Optional[str] = None,
        source: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MemoryItem:
        pass

    @abstractmethod
    async def retrieve(
        self, user_id: str, query: str, limit: int = 10, category: Optional[str] = None
    ) -> List[MemoryItem]:
        pass

    @abstractmethod
    async def get_categories(self, user_id: str) -> List[MemoryCategory]:
        pass

    @abstractmethod
    async def delete(self, item_id: str) -> bool:
        pass

    @abstractmethod
    async def get_user_memory(self, user_id: str) -> Dict[str, Any]:
        pass

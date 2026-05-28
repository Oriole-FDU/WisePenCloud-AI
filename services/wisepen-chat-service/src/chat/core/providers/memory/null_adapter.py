from __future__ import annotations

from typing import Any, Dict, List, Optional

from chat.domain.entities import ChatMessage
from chat.domain.interfaces import MemoryProvider


class NullMemoryAdapter(MemoryProvider):
    async def search(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
        score_threshold: Optional[float] = None,
    ) -> List[str]:
        return []

    async def add_interaction(self, user_id: str, messages: List[ChatMessage]):
        return None

    async def get_all(self, user_id: str) -> List[Dict[str, Any]]:
        return []

    async def delete_memory(self, memory_id: str, user_id: str) -> None:
        return None

    async def delete_all_for_user(self, user_id: str) -> None:
        return None

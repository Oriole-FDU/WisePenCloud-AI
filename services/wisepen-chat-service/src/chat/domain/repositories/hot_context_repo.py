from abc import ABC, abstractmethod
from typing import List
from chat.domain.entities import ChatMessage


class HotContextRepository(ABC):
    """热数据：短期上下文仓储接口 (Redis)"""

    @abstractmethod
    async def append_messages(self, session_id: str, messages: List[ChatMessage], max_length: int = 50) -> None: pass

    @abstractmethod
    async def get_recent_context(self, session_id: str) -> List[ChatMessage]: pass

    @abstractmethod
    async def load_messages(self, session_id: str, messages: List[ChatMessage]) -> None:
        """用给定消息重建 Redis 热缓存；传入空列表表示清空"""
        pass


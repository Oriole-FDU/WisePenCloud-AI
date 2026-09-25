from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from chat.domain.entities import ChatMessage


class MemoryProvider(ABC):

    @abstractmethod
    async def search(
        self,
        user_id: str,
        query: str,
        limit: int = 5,
        score_threshold: Optional[float] = None,
    ) -> List[str]:
        """按语义相似度门槛筛选，最多返回 limit 条；None 不设门槛，limit 为 0 时不搜索。"""
        pass

    @abstractmethod
    async def add_interaction(self, user_id: str, messages: List[ChatMessage]):
        """将新一轮对话摄入长期记忆"""
        pass

    @abstractmethod
    async def get_all(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """返回指定用户最多 limit 条记忆（包含 id、memory、metadata 等字段），不提供总数。"""
        pass

    @abstractmethod
    async def delete_memory(self, memory_id: str, user_id: str) -> None:
        """删除单条记忆"""
        pass

    @abstractmethod
    async def delete_all_for_user(self, user_id: str) -> None:
        """清空指定用户的全部记忆"""
        pass

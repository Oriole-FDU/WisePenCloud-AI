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
        """根据语义相似度检索相关事实。score_threshold 非 None 时作为 mem0 的语义阈值过滤（limit 仍限制最大返回条数）"""
        pass

    @abstractmethod
    async def add_interaction(self, user_id: str, messages: List[ChatMessage]):
        """将新一轮对话摄入长期记忆"""
        pass

    @abstractmethod
    async def get_all(self, user_id: str) -> List[Dict[str, Any]]:
        """返回指定用户的全部记忆条目（包含 id、memory、metadata 等字段）"""
        pass

    @abstractmethod
    async def delete_memory(self, memory_id: str, user_id: str) -> None:
        """删除单条记忆"""
        pass

    @abstractmethod
    async def delete_all_for_user(self, user_id: str) -> None:
        """清空指定用户的全部记忆"""
        pass
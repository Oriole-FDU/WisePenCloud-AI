from abc import ABC, abstractmethod
from typing import List, Optional, Tuple
from datetime import datetime
from chat.domain.entities import ChatMessage


class MessageRepository(ABC):
    """冷数据：历史消息仓储接口 (MongoDB)"""

    @abstractmethod
    async def save_many(self, messages: List[ChatMessage]) -> None: pass

    @abstractmethod
    async def get_by_session(
        self,
        session_id: str,
        after: datetime = None,
        limit: int = 50,
    ) -> List[ChatMessage]:
        pass

    @abstractmethod
    async def get_page_for_ui(
        self,
        session_id: str,
        page: int,
        size: int,
    ) -> Tuple[List[ChatMessage], int]:
        """
        按对话回合分页拉取消息，以 user 消息为回合锚点。
        page=1 返回最新的 `size` 个回合，页码递增返回更早的回合。
        每个回合包含 1 条 user 消息及其后续的 assistant + tool 消息。
        返回 (本页原始消息按时间正序, 总回合数)。
        """
        pass

    @abstractmethod
    async def full_text_search(
        self,
        keyword: str,
        session_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 10,
    ) -> List[ChatMessage]:
        """对 content 字段进行全文检索（依赖 MongoDB Text Index）"""
        pass

    @abstractmethod
    async def get_after_time(
        self,
        session_id: str,
        after: datetime,
        limit: int,
    ) -> List[ChatMessage]:
        pass

from abc import ABC, abstractmethod
from typing import Dict, List

from chat.domain.entities import ChatMessageFeedback


class MessageFeedbackRepository(ABC):
    """消息点赞/点踩记录仓储接口 (MongoDB)"""

    @abstractmethod
    async def upsert(self, feedback: ChatMessageFeedback) -> None: pass

    @abstractmethod
    async def delete(self, user_id: str, message_id: str) -> None: pass

    @abstractmethod
    async def list_for_user_messages(self, user_id: str, message_ids: List[str]) -> Dict[str, str]:
        """返回 message_id -> feedback 值 的映射，仅含当前用户已反馈的条目"""
        pass

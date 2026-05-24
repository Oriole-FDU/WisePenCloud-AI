from abc import ABC, abstractmethod
from typing import List, Optional
from chat.domain.entities import ChatAttachment


class ChatAttachmentRepository(ABC):
    """会话附件仓储接口（MongoDB）"""

    @abstractmethod
    async def create(self, attachment: ChatAttachment) -> ChatAttachment:
        pass

    @abstractmethod
    async def get_by_object_key(self, object_key: str) -> Optional[ChatAttachment]:
        pass

    @abstractmethod
    async def confirm_upload(self, object_key: str) -> Optional[ChatAttachment]:
        pass

    @abstractmethod
    async def get_uploaded_by_session(self, session_id: str) -> List[ChatAttachment]:
        pass

    @abstractmethod
    async def mark_pending_as_deleted(self, session_id: str) -> None:
        pass

    @abstractmethod
    async def mark_deleted(self, object_key: str) -> bool:
        """标记附件为已删除，返回 True 表示更新成功"""
        pass

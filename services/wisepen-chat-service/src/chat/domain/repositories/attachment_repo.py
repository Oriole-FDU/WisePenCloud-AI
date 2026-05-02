from abc import ABC, abstractmethod
from typing import List, Optional

from chat.domain.entities import ChatAttachment


class AttachmentRepository(ABC):
    """聊天附件仓储接口"""

    @abstractmethod
    async def create(self, attachment: ChatAttachment) -> ChatAttachment:
        pass

    @abstractmethod
    async def get_by_attachment_id(self, attachment_id: str) -> Optional[ChatAttachment]:
        pass

    @abstractmethod
    async def save(self, attachment: ChatAttachment) -> ChatAttachment:
        pass

    @abstractmethod
    async def list_by_session(self, session_id: str, user_id: str) -> List[ChatAttachment]:
        pass

    @abstractmethod
    async def list_by_attachment_ids(
        self,
        session_id: str,
        user_id: str,
        attachment_ids: List[str],
    ) -> List[ChatAttachment]:
        pass

    @abstractmethod
    async def update_config(
        self,
        attachment_id: str,
        user_id: str,
        save_to_library: bool,
        context_enabled: bool,
    ) -> ChatAttachment:
        pass

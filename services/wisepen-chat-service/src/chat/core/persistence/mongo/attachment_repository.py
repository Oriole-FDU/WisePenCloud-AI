from datetime import datetime, timezone
from typing import List, Optional

from chat.domain.entities import ChatAttachment
from chat.domain.error_codes import ChatErrorCode
from chat.domain.repositories import AttachmentRepository
from common.core.exceptions import ServiceException


class MongoAttachmentRepository(AttachmentRepository):
    """聊天附件仓储的 MongoDB 实现"""

    async def create(self, attachment: ChatAttachment) -> ChatAttachment:
        await attachment.insert()
        return attachment

    async def get_by_attachment_id(self, attachment_id: str) -> Optional[ChatAttachment]:
        return await ChatAttachment.find_one(ChatAttachment.attachment_id == attachment_id)

    async def save(self, attachment: ChatAttachment) -> ChatAttachment:
        attachment.updated_at = datetime.now(timezone.utc)
        await attachment.save()
        return attachment

    async def list_by_session(self, session_id: str, user_id: str) -> List[ChatAttachment]:
        return await ChatAttachment.find(
            ChatAttachment.session_id == session_id,
            ChatAttachment.user_id == user_id,
        ).sort("-created_at").to_list()

    async def list_by_attachment_ids(
        self,
        session_id: str,
        user_id: str,
        attachment_ids: List[str],
    ) -> List[ChatAttachment]:
        return await ChatAttachment.find(
            ChatAttachment.session_id == session_id,
            ChatAttachment.user_id == user_id,
            {"attachment_id": {"$in": attachment_ids}},
        ).sort("-created_at").to_list()

    async def update_config(
        self,
        attachment_id: str,
        user_id: str,
        save_to_library: bool,
        context_enabled: bool,
    ) -> ChatAttachment:
        attachment = await ChatAttachment.find_one(
            ChatAttachment.attachment_id == attachment_id,
            ChatAttachment.user_id == user_id,
        )
        if attachment is None:
            raise ServiceException(ChatErrorCode.ATTACHMENT_NOT_FOUND)

        attachment.save_to_library = save_to_library
        attachment.context_enabled = context_enabled
        return await self.save(attachment)

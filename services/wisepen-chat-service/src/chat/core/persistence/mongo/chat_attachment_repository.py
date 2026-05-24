from typing import List, Optional
from datetime import datetime, timezone
from chat.domain.repositories import ChatAttachmentRepository
from chat.domain.entities import ChatAttachment


class MongoChatAttachmentRepository(ChatAttachmentRepository):

    async def create(self, attachment: ChatAttachment) -> ChatAttachment:
        await attachment.insert()
        return attachment

    async def get_by_object_key(self, object_key: str) -> Optional[ChatAttachment]:
        return await ChatAttachment.find_one(
            ChatAttachment.object_key == object_key
        )

    async def confirm_upload(self, object_key: str) -> Optional[ChatAttachment]:
        """将 pending 附件标记为 uploaded，仅在当前状态为 pending 时生效"""
        attachment = await ChatAttachment.find_one(
            ChatAttachment.object_key == object_key,
            ChatAttachment.status == "pending",
        )
        if attachment is None:
            return None
        attachment.status = "uploaded"
        attachment.updated_at = datetime.now(timezone.utc)
        await attachment.save()
        return attachment

    async def get_uploaded_by_session(self, session_id: str) -> List[ChatAttachment]:
        """拉取 session 下所有已上传成功的附件"""
        return await ChatAttachment.find(
            ChatAttachment.session_id == session_id,
            ChatAttachment.status == "uploaded",
        ).to_list()

    async def mark_pending_as_deleted(self, session_id: str) -> None:
        """将 session 下未确认上传的待处理记录批量标记已删除"""
        await ChatAttachment.find(
            ChatAttachment.session_id == session_id,
            ChatAttachment.status == "pending",
        ).update({"$set": {"status": "deleted", "updated_at": datetime.now(timezone.utc)}})

    async def mark_deleted(self, object_key: str) -> bool:
        """标记单个附件为已删除，仅在 uploaded 状态时生效"""
        attachment = await ChatAttachment.find_one(
            ChatAttachment.object_key == object_key,
            ChatAttachment.status == "uploaded",
        )
        if attachment is None:
            return False
        attachment.status = "deleted"
        attachment.updated_at = datetime.now(timezone.utc)
        await attachment.save()
        return True

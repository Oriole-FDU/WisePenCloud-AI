from typing import List, Optional
from common.clients.file_storage import FileStorageClient, UploadInitRequest
from common.logger import logger
from chat.api.schemas.attachment import (
    InitAttachmentUploadRequest,
    InitAttachmentUploadResponse,
    ConfirmUploadResponse,
    DeleteAttachmentResponse,
    GetAttachmentPreviewUrlResponse,
)
from chat.api.schemas.chat import AttachmentRefRequest
from chat.domain.repositories import ChatAttachmentRepository
from chat.domain.entities import ChatAttachment


class AttachmentService:
    """附件上传、绑定与清理"""

    def __init__(
        self,
        file_storage_client: FileStorageClient,
        chat_attachment_repo: ChatAttachmentRepository,
    ) -> None:
        self._file_storage = file_storage_client
        self._repo = chat_attachment_repo

    async def init_upload(
        self, user_id: str, req: InitAttachmentUploadRequest
    ) -> InitAttachmentUploadResponse:
        """向file_storage申请上传凭证，并且向MongoDB中预写状态为pending的文件记录"""
        upload_req = UploadInitRequest(
            md5=req.md5,
            extension=req.extension,
            scene="CHAT_ATTACHMENT",
            biz_path=f"{user_id}/{req.session_id}",
            expected_size=req.file_size,
        )
        resp = await self._file_storage.init_upload(upload_req) # OSS回传的上传凭证

        # 秒传：同 MD5 文件已存在，OSS 侧已克隆完成，无需前端 PUT
        if resp.flash_uploaded:
            attachment = ChatAttachment(
                session_id=req.session_id,
                user_id=user_id,
                object_key=resp.object_key,
                original_name=req.filename,
                extension=req.extension,
                file_size=req.file_size,
                mime_type=req.mime_type,
                status="uploaded",
            )
            await self._repo.create(attachment)
            logger.info(
                f"attachment created objectKey={resp.object_key} "
                f'filename="{req.filename}" fileSize={req.file_size} '
                f"status=uploaded reason=flashUploaded"
            )
            return InitAttachmentUploadResponse(
                object_key=resp.object_key,
                put_url="",
                callback_header="",
                flash_uploaded=True,
            )

        attachment = ChatAttachment(
            session_id=req.session_id,
            user_id=user_id,
            object_key=resp.object_key,
            original_name=req.filename,
            extension=req.extension,
            file_size=req.file_size,
            mime_type=req.mime_type,
            status="pending",
        )
        await self._repo.create(attachment)
        logger.info(
            f"attachment created objectKey={resp.object_key} "
            f'filename="{req.filename}" fileSize={req.file_size} status=pending'
        )

        return InitAttachmentUploadResponse(
            object_key=resp.object_key,
            put_url=resp.put_url,
            callback_header=resp.callback_header,
        )

    async def confirm_upload(self, object_key: str) -> ConfirmUploadResponse:
        """确认上传附件：前端直传OSS成功后回调，将DB中文件状态由pending转为uploaded"""
        attachment = await self._repo.confirm_upload(object_key)
        if attachment is None:
            logger.warning(
                f"attachmentConfirm failed objectKey={object_key} "
                f"status=not_found_or_not_pending"
            )
            return ConfirmUploadResponse(object_key=object_key, status="not_found_or_not_pending")
        logger.info(
            f"attachmentStatus changed objectKey={object_key} "
            f"from=pending to=uploaded"
        )
        return ConfirmUploadResponse(object_key=object_key, status=attachment.status)

    async def resolve_session_attachments(
        self, session_id: str
    ) -> Optional[List[AttachmentRefRequest]]:
        """获取已uploaded的附件引用列表，同时将session_id下的仍处于pending状态的附件状态置为deleted"""
        await self._repo.mark_pending_as_deleted(session_id)
        records = await self._repo.get_uploaded_by_session(session_id)
        if not records:
            return None
        return [
            AttachmentRefRequest(object_key=r.object_key, filename=r.original_name)
            for r in records
        ]

    async def get_session_attachments_meta(
        self, session_id: str
    ) -> Optional[List[ChatAttachment]]:
        """获取 session 下所有 uploaded 附件的完整元数据"""
        await self._repo.mark_pending_as_deleted(session_id)
        records = await self._repo.get_uploaded_by_session(session_id)
        return records if records else None

    async def delete_attachment(self, object_key: str) -> DeleteAttachmentResponse:
        """软删除 OSS 文件，并更新 MongoDB 记录为 deleted"""
        updated = await self._repo.mark_deleted(object_key)
        if not updated:
            logger.warning(
                f"attachmentDelete skipped objectKey={object_key} "
                f"reason=notUploadedOrAlreadyDeleted"
            )
        else:
            logger.info(f"attachment deleted objectKey={object_key}")
        await self._file_storage.delete_file(object_key)
        return DeleteAttachmentResponse(success=True)

    async def get_preview_url(
        self, object_key: str, duration_seconds: int = 900
    ) -> GetAttachmentPreviewUrlResponse:
        """获取 OSS 文件临时下载链接"""
        url = await self._file_storage.get_download_url(object_key, duration_seconds)
        return GetAttachmentPreviewUrlResponse(url=url)

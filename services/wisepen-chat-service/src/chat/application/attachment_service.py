import os
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from fastapi import UploadFile

from common.clients.file_storage import FileStorageClient, UploadInitRequest
from common.logger import logger
from chat.api.schemas.attachment import (
    InitLargeUploadRequest,
    InitLargeUploadResponse,
    UploadSmallResponse,
    DeleteAttachmentResponse,
    GetAttachmentPreviewUrlResponse,
)
from chat.api.schemas.chat import AttachmentRefRequest
from chat.domain.repositories import SessionRepository
from chat.domain.entities import AttachmentMeta


class AttachmentService:
    """附件上传、绑定与清理"""

    def __init__(
        self,
        file_storage_client: FileStorageClient,
        session_repo: SessionRepository,
    ) -> None:
        self._file_storage = file_storage_client
        self._session_repo = session_repo

    async def init_large_upload(
        self, user_id: str, req: InitLargeUploadRequest
    ) -> InitLargeUploadResponse:
        """大文件上传：向 Java 申请 OSS 预签名 URL，append 到 ChatSession.attachments"""
        upload_req = UploadInitRequest(
            md5="",
            extension=req.extension,
            scene="CHAT_ATTACHMENT",
            biz_path=f"{user_id}/{req.session_id}",
            expected_size=req.file_size,
        )
        resp = await self._file_storage.init_upload(upload_req)

        session = await self._session_repo.get_session(req.session_id)
        meta = AttachmentMeta(
            object_key=resp.object_key,
            original_name=req.filename,
            extension=req.extension,
            file_size=req.file_size,
        )
        session.attachments.append(meta)
        session.updated_at = datetime.now(timezone.utc)
        await session.save()

        logger.info(
            f"attachment created objectKey={resp.object_key} "
            f'filename="{req.filename}" fileSize={req.file_size} type=large'
        )
        return InitLargeUploadResponse(
            object_key=resp.object_key,
            put_url=resp.put_url,
            callback_header=resp.callback_header,
        )

    async def upload_small_file(
        self, user_id: str, session_id: str, file: UploadFile,
    ) -> UploadSmallResponse:
        """小文件上传：服务端提取元数据 → Java 预签名 URL → PUT OSS → 同步返回"""
        content = await file.read()
        file_size = len(content)
        filename = file.filename or "unknown"
        extension = os.path.splitext(filename)[1].lstrip(".") or "bin"
        mime_type = file.content_type or "application/octet-stream"

        upload_req = UploadInitRequest(
            md5="",
            extension=extension,
            scene="CHAT_ATTACHMENT",
            biz_path=f"{user_id}/{session_id}",
            expected_size=file_size,
        )
        resp = await self._file_storage.init_upload(upload_req)

        headers = {}
        if resp.callback_header:
            headers["x-oss-callback"] = resp.callback_header
        async with httpx.AsyncClient() as client:
            put_resp = await client.put(resp.put_url, content=content, headers=headers)
            put_resp.raise_for_status()

        session = await self._session_repo.get_session(session_id)
        meta = AttachmentMeta(
            object_key=resp.object_key,
            original_name=filename,
            extension=extension,
            file_size=file_size,
            mime_type=mime_type,
        )
        session.attachments.append(meta)
        session.updated_at = datetime.now(timezone.utc)
        await session.save()

        logger.info(
            f"attachment created objectKey={resp.object_key} "
            f'filename="{filename}" fileSize={file_size} type=small'
        )
        return UploadSmallResponse(object_key=resp.object_key)

    async def resolve_session_attachments(
        self, session_id: str
    ) -> Optional[List[AttachmentRefRequest]]:
        """获取会话下全部附件引用列表（直接从 ChatSession.attachments 读取）"""
        session = await self._session_repo.get_session(session_id)
        if not session.attachments:
            return None
        return [
            AttachmentRefRequest(object_key=a.object_key, filename=a.original_name)
            for a in session.attachments
        ]

    async def get_session_attachments_meta(
        self, session_id: str
    ) -> Optional[List[AttachmentMeta]]:
        """获取 session 下所有附件的完整元数据"""
        session = await self._session_repo.get_session(session_id)
        return session.attachments if session.attachments else None

    async def delete_attachment(self, object_key: str) -> DeleteAttachmentResponse:
        """从 ChatSession.attachments 中移除附件条目"""
        parts = object_key.split("/")
        if len(parts) < 4:
            logger.warning(
                f"attachment delete skipped objectKey={object_key} "
                f"reason=invalidObjectKeyFormat"
            )
            return DeleteAttachmentResponse(success=True)

        session_id = parts[2]
        session = await self._session_repo.get_session(session_id)
        original_count = len(session.attachments)
        session.attachments = [
            a for a in session.attachments if a.object_key != object_key
        ]

        if len(session.attachments) == original_count:
            logger.warning(
                f"attachment delete skipped objectKey={object_key} "
                f"reason=notFoundInSession"
            )
        else:
            session.updated_at = datetime.now(timezone.utc)
            await session.save()
            logger.info(f"attachment deleted objectKey={object_key}")

        return DeleteAttachmentResponse(success=True)

    async def get_preview_url(
        self, object_key: str, duration_seconds: int = 900
    ) -> GetAttachmentPreviewUrlResponse:
        """获取 OSS 文件临时下载链接"""
        url = await self._file_storage.get_download_url(object_key, duration_seconds)
        return GetAttachmentPreviewUrlResponse(url=url)

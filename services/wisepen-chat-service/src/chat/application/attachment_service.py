from datetime import datetime, timezone
from typing import List, Optional

from chat.service_client.file_storage_service_client import UploadInitRequest as StorageInitReq

from common.logger import logger
from chat.api.schemas.attachment import (
    InitUploadRequest,
    InitUploadResponse,
    DeleteAttachmentResponse,
)
from chat.api.schemas.chat import AttachmentRefRequest
from chat.domain.repositories import SessionRepository
from chat.domain.entities import AttachmentMeta, ResourceRef

_ATTACHMENT_SCENE = "PRIVATE_ATTACHMENT"


class AttachmentService:
    """附件上传编排——前端直传 OSS，Python 只做元数据编排"""

    def __init__(
        self,
        session_repo: SessionRepository,
        file_storage_client,
    ) -> None:
        self._session_repo = session_repo
        self._file_storage = file_storage_client

    async def init_upload(
        self,
        user_id: str,
        req: InitUploadRequest,
    ) -> InitUploadResponse:
        """初始化附件直传：根据 enable_library 调不同 Java API，返回 OSS 预签名 URL。

        enable_library=false: 调 storage init_upload（attachment scene）
        enable_library=true:  调 document /uploadDoc（document scene）
        两种路径的 biz_path 均为 {user_id}/{session_id}，确保 objectKey 含 session_id。
        """
        biz_path = f"{user_id}/{req.session_id}"

        if req.enable_library:
            object_key, put_url, callback_header = await self._init_document_upload(
                user_id, req, biz_path
            )
        else:
            object_key, put_url, callback_header = await self._init_attachment_upload(
                req, biz_path
            )

        session = await self._session_repo.get_session_for_user(req.session_id, user_id)
        meta = AttachmentMeta(
            object_key=f"{req.session_id}/attachments/{req.filename}",
            oss_object_key=object_key,
            original_name=req.filename,
            extension=req.extension,
            file_size=req.file_size,
            mime_type=None,
        )
        session.attachments.append(meta)
        session.updated_at = datetime.now(timezone.utc)
        await session.save()

        logger.info(
            "attachmentUpload succeeded userId={} fileSize={} enableLibrary={}",
            user_id, req.file_size, req.enable_library,
        )
        return InitUploadResponse(
            object_key=object_key,
            put_url=put_url,
            callback_header=callback_header,
        )

    async def _init_attachment_upload(
        self, req: InitUploadRequest, biz_path: str
    ) -> tuple:
        storage_req = StorageInitReq(
            md5=req.md5,
            extension=req.extension,
            scene=_ATTACHMENT_SCENE,
            biz_path=biz_path,
            expected_size=req.file_size,
        )
        resp = await self._file_storage.init_upload(storage_req)
        return resp.object_key, resp.put_url, resp.callback_header

    async def _init_document_upload(
        self, user_id: str, req: InitUploadRequest, biz_path: str
    ) -> tuple:
        return await self._file_storage.init_document_upload(
            user_id=user_id,
            filename=req.filename,
            extension=req.extension,
            md5=req.md5,
            expected_size=req.file_size,
            biz_path=biz_path,
        )

    async def add_resources(
        self, user_id: str, session_id: str,
        resources: list[dict],
    ):
        """添加资源到会话——仅记录 resource_id 和 resource_type，不获取元数据。"""
        session = await self._session_repo.get_session_for_user(session_id, user_id)

        for item in resources:
            rid = item["resource_id"]
            rtype = item["resource_type"]

            existing = next(
                (r for r in session.resource_refs if r.resource_id == rid), None
            )
            if existing is not None:
                if not existing.deleted:
                    continue
                existing.deleted = False
                existing.loaded_at = datetime.now(timezone.utc)
                continue

            ref = ResourceRef(
                resource_id=rid,
                resource_type=rtype,
                loaded_at=datetime.now(timezone.utc),
            )
            session.resource_refs.append(ref)

        session.updated_at = datetime.now(timezone.utc)
        await session.save()
        logger.info("resourceBinding finished userId={} count={}", user_id, len(resources))
        return session

    async def delete_resources(
        self, user_id: str, session_id: str, resource_ids: list[str],
    ):
        """软删除资源引用。沙箱文件不清理。"""
        session = await self._session_repo.get_session_for_user(session_id, user_id)
        changed = False
        for ref in session.resource_refs:
            if ref.resource_id in resource_ids and not ref.deleted:
                ref.deleted = True
                changed = True

        if changed:
            session.updated_at = datetime.now(timezone.utc)
            await session.save()

        logger.info("resourceBinding deleted userId={} count={} changed={}", user_id, len(resource_ids), changed)
        return session

    async def resolve_session_attachments(
        self, session_id: str
    ) -> Optional[List[AttachmentRefRequest]]:
        session = await self._session_repo.get_session(session_id)
        if not session.attachments:
            return None
        return [
            AttachmentRefRequest(object_key=a.object_key, filename=a.original_name)
            for a in session.attachments
            if not a.deleted
        ]

    async def get_session_context(
        self, session_id: str, user_id: str,
    ) -> tuple:
        """一次查询返回会话上下文所需数据：附件元数据（dict 列表）和活跃资源引用。"""
        session = await self._session_repo.get_session_for_user(session_id, user_id)

        active_attachments = [a for a in session.attachments if not a.deleted]
        attachments_meta = [
            {
                "object_key": a.object_key,
                "original_name": a.original_name,
                "extension": a.extension,
                "file_size": a.file_size,
                "mime_type": a.mime_type,
            }
            for a in active_attachments
        ] if active_attachments else None

        active_resources = [r for r in session.resource_refs if not r.deleted] or None

        return attachments_meta, active_resources

    async def delete_attachment(
        self, user_id: str, session_id: str, filename: str,
    ) -> DeleteAttachmentResponse:
        session = await self._session_repo.get_session_for_user(session_id, user_id)
        object_key = f"{session_id}/attachments/{filename}"

        matched = next(
            (a for a in session.attachments
             if a.object_key == object_key and not a.deleted),
            None,
        )
        if matched is None:
            logger.warning("attachmentDelete skipped userId={} objectKey={}", user_id, object_key)
            return DeleteAttachmentResponse(success=False)
        else:
            matched.deleted = True
            session.updated_at = datetime.now(timezone.utc)
            await session.save()
            logger.info("attachmentDelete succeeded userId={} objectKey={}", user_id, object_key)

        return DeleteAttachmentResponse(success=True)

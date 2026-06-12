import base64
import shlex
from datetime import datetime, timezone
from typing import List, Optional

import httpx
from chat.service_client.file_storage_service_client import UploadInitRequest as StorageInitReq

from chat.core.config.app_settings import settings
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
        rpc_client,
    ) -> None:
        self._session_repo = session_repo
        self._file_storage = file_storage_client
        self._rpc = rpc_client
        self._gateway_url = settings.AIO_GATEWAY_BASE_URL.rstrip("/")
        self._from_source = settings.FROM_SOURCE_SECRET
        self._sandbox_timeout = 30.0

    async def _shell_exec(self, command: str, user_id: str, session_id: str) -> None:
        """通过 AIO Gateway 在沙箱中执行 shell 命令。失败时 raise。"""
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self._sandbox_timeout)
        ) as client:
            resp = await client.post(
                f"{self._gateway_url}/v1/aio/shell/exec",
                json={"command": command, "exec_dir": "/workspace"},
                headers={
                    "X-User-Id": user_id,
                    "X-Session-Id": session_id,
                    "X-From-Source": self._from_source,
                },
            )
            resp.raise_for_status()

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
        data = await self._rpc.post(
            "wisepen-document-service",
            "/document/uploadDoc",
            json={
                "filename": req.filename,
                "extension": req.extension,
                "md5": req.md5,
                "expectedSize": req.file_size,
                "bizPath": biz_path,
            },
            headers={"X-WP-User-Id": user_id},
        )
        object_key = data.get("objectKey")
        put_url = data.get("putUrl")
        if not object_key or not put_url:
            raise ValueError(f"uploadDoc response missing required fields: {data!r}")
        return str(object_key), str(put_url), str(data.get("callbackHeader", ""))

    async def add_resources(
        self, user_id: str, session_id: str,
        resources: list[dict],
    ):
        """添加文档库资源到会话。根据 resource_type 分发到对应服务。"""
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

            if rtype == "note":
                ref = await self._add_note_resource(user_id, session_id, rid)
            elif rtype == "document":
                ref = await self._add_document_resource(user_id, session_id, rid)
            else:
                logger.warning("resourceBind typeUnknown resourceId={} type={}", rid, rtype)
                continue

            if ref is not None:
                session.resource_refs.append(ref)

        session.updated_at = datetime.now(timezone.utc)
        await session.save()
        logger.info("resourceBinding finished userId={} count={}", user_id, len(resources))
        return session

    async def _add_note_resource(
        self, user_id: str, session_id: str, rid: str
    ):
        """笔记资源：获取 plainText → 写入沙箱 .txt。"""
        note_info = await self._rpc.get(
            "wisepen-note-service",
            "/note/getNoteInfo",
            params={"resourceId": rid},
            headers={"X-WP-User-Id": user_id},
        )
        resource_info = note_info.get("resourceInfo", {})
        note_data = note_info.get("noteInfo", {})
        name = resource_info.get("resourceName", "unnamed")
        plain_text = note_data.get("plainText", "")

        ref = ResourceRef(
            resource_id=rid,
            resource_type="note",
            name=name,
            extension="note",
        )

        if plain_text:
            try:
                b64 = base64.b64encode(plain_text.encode("utf-8")).decode("ascii")
                safe_name = shlex.quote(name)
                safe_rid = shlex.quote(rid)
                cmd = (
                    f"mkdir -p /workspace/resources/{safe_rid} && "
                    f"echo {shlex.quote(b64)} | base64 -d > /workspace/resources/{safe_rid}/{safe_name}.txt"
                )
                await self._shell_exec(cmd, user_id, session_id)
                ref.loaded_at = datetime.now(timezone.utc)
                logger.info("noteResource loaded resourceId={} textLength={}", rid, len(plain_text))
            except Exception as e:
                logger.opt(exception=e).warning("noteResource loadFailed resourceId={}", rid)

        return ref

    async def _add_document_resource(
        self, user_id: str, session_id: str, rid: str
    ):
        """文档资源：下载源文件 + 写入解析文本到沙箱。"""
        doc_info = await self._rpc.get(
            "wisepen-document-service",
            "/document/getDocInfo",
            params={"resourceId": rid},
            headers={"X-WP-User-Id": user_id},
        )
        resource_info = doc_info.get("resourceInfo", {})
        document_info = doc_info.get("documentInfo", {})
        name = resource_info.get("resourceName", "unnamed")
        upload_meta = document_info.get("uploadMeta", {})
        extension = str(upload_meta.get("fileType", "bin")).lower()
        object_key = document_info.get("sourceObjectKey", "")

        if not object_key:
            logger.warning("documentResource bindFailed resourceId={} reason=noSourceObjectKey", rid)
            return None

        ref = ResourceRef(
            resource_id=rid,
            resource_type="document",
            name=name,
            extension=extension,
        )

        try:
            download_url = await self._file_storage.get_download_url(object_key)
            safe_name = shlex.quote(name)
            safe_ext = shlex.quote(extension)
            safe_rid = shlex.quote(rid)
            filename = f"{name}.{extension}"
            cmd = (
                f"mkdir -p /workspace/resources/{safe_rid} && "
                f"curl -sS {shlex.quote(download_url)} -o /workspace/resources/{safe_rid}/{safe_name}.{safe_ext}"
            )
            await self._shell_exec(cmd, user_id, session_id)

            # 尝试获取解析文本（Java getDocText 就绪前静默跳过）
            try:
                doc_text = await self._rpc.get(
                    "wisepen-document-service",
                    "/document/getDocText",
                    params={"resourceId": rid},
                    headers={"X-WP-User-Id": user_id},
                )
                raw_text = doc_text.get("rawText", "")
                if raw_text:
                    b64 = base64.b64encode(raw_text.encode("utf-8")).decode("ascii")
                    cmd = f"echo {shlex.quote(b64)} | base64 -d > /workspace/resources/{safe_rid}/{safe_name}.txt"
                    await self._shell_exec(cmd, user_id, session_id)
            except Exception as e:
                logger.info("documentResource getDocText skipped resourceId={}", rid)

            ref.loaded_at = datetime.now(timezone.utc)
            logger.info("documentResource loaded resourceId={} objectKey={}", rid, object_key)
        except Exception as e:
            logger.opt(exception=e).warning("documentResource loadFailed resourceId={}", rid)

        return ref

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

    async def confirm_upload(
        self, user_id: str, session_id: str, oss_object_key: str,
    ):
        """确认附件上传完成：从 OSS 下载到沙箱，标记 uploaded_at。"""
        session = await self._session_repo.get_session_for_user(session_id, user_id)

        matched = next(
            (a for a in session.attachments
             if a.oss_object_key == oss_object_key and a.uploaded_at is None and not a.deleted),
            None,
        )
        if matched is None:
            logger.warning("attachmentUpload confirmNoMatch userId={} objectKey={}", user_id, oss_object_key)
            return session

        download_url = await self._file_storage.get_download_url(oss_object_key)
        safe_filename = shlex.quote(matched.original_name)
        cmd = (
            f"mkdir -p /workspace/attachments && "
            f"curl -sS {shlex.quote(download_url)} -o /workspace/attachments/{safe_filename}"
        )
        await self._shell_exec(cmd, user_id, session_id)
        matched.uploaded_at = datetime.now(timezone.utc)
        session.updated_at = datetime.now(timezone.utc)
        await session.save()
        logger.info("attachmentUpload confirmed userId={} objectKey={}", user_id, oss_object_key)
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

    async def get_session_attachments_meta(
        self, session_id: str
    ) -> Optional[List[AttachmentMeta]]:
        session = await self._session_repo.get_session(session_id)
        active = [a for a in session.attachments if not a.deleted]
        return active if active else None

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

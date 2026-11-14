import uuid
from typing import List
from datetime import datetime, timezone

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from chat.domain.entities import ResourceAttachmentRef, TemporaryAttachmentRef
from chat.domain.repositories import SessionRepository
from chat.service_client import FileStorageClient, ResourceClient
from common.core.domain import R
from common.logger import info, warning
from common.security import SecurityContextHolder, require_login

from chat.api.schemas.attachment import (
    AddResourceAttachmentsRequest,
    DeleteAttachmentRequest,
    InitUploadRequest,
    InitUploadResponse,
)
from chat.container import Container

router = APIRouter()


@router.post(
    "/initUploadTemporaryAttachment",
    response_model=R[InitUploadResponse],
    summary="初始化临时附件上传",
    description="""
- 用途：为当前会话中的临时 AI 附件申请对象存储直传凭证。
- 请求：session_id 指定目标会话；filename、extension、file_size 和 md5 描述待上传文件；enable_library 当前不改变处理流程。
- 约束：当前用户必须已登录；目标会话必须属于当前用户；文件名、后缀、大小和 MD5 必须满足请求模型约束。
- 处理：向文件存储服务申请 PRIVATE_AI_ATTACHMENT 上传凭证，生成会话内 attachment_id，并把临时附件引用追加到会话；不上传文件字节，不创建资源附件。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；请求参数校验失败 -> ResultCode.PARAM_ERROR；会话不存在或不属于当前用户 -> ChatErrorCode.SESSION_NOT_FOUND；文件存储服务调用失败 -> ResultCode.SYSTEM_ERROR。
- 响应：返回附件 ID、objectKey、秒传状态、预签名上传 URL 和回调 header；flash_uploaded=true 时无需客户端 PUT。
""",
)
@inject
async def init_temp_attachment_upload(
    req: InitUploadRequest,
    user_id: str = Depends(require_login),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
    file_storage_client: FileStorageClient = Depends(Provide[Container.file_storage_client])
):
    session = await session_repo.get_session_for_user(req.session_id, user_id)
    biz_tag = f"{user_id}/{req.session_id}"

    init_upload_res = await file_storage_client.init_upload(
        md5=req.md5,
        extension=req.extension,
        scene="PRIVATE_AI_ATTACHMENT",
        biz_tag=biz_tag,
        provider_code=None,
        expected_size=req.file_size,
    )

    # 构建 attachment_id
    attachment_id = uuid.uuid4().hex

    ref = TemporaryAttachmentRef(
        attachment_id=attachment_id, attachment_name=req.filename,
        object_key=init_upload_res.object_key,
        extension=req.extension,
        file_size=req.file_size,
        mime_type=None,
    )
    session.temporary_attachment_refs.append(ref)
    session.updated_at = datetime.now(timezone.utc)
    await session.save()

    info("temporary attachment upload initialized", user_id=user_id)

    return R.success(data=InitUploadResponse(
        attachment_id=attachment_id,
        object_key=init_upload_res.object_key,
        put_url=init_upload_res.put_url,
        callback_header=init_upload_res.callback_header,
        flash_uploaded=init_upload_res.flash_uploaded,
    ))


@router.post(
    "/addResourceAttachments",
    response_model=R[List[str]],
    status_code=200,
    summary="添加资源附件",
    description="""
- 用途：将用户可访问的资源作为会话附件加入当前聊天会话。
- 请求：session_id 指定目标会话；resource_ids 指定要加入的资源 ID 列表。
- 约束：当前用户必须已登录；目标会话必须属于当前用户；资源列表不能为空；用户必须能通过资源服务读取目标资源信息。
- 处理：逐个读取资源信息，已存在的资源附件会刷新名称、类型并恢复为未删除；不存在时创建新的资源附件引用；不复制资源文件。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；请求参数校验失败 -> ResultCode.PARAM_ERROR；会话不存在或不属于当前用户 -> ChatErrorCode.SESSION_NOT_FOUND；资源服务调用失败 -> ResultCode.SYSTEM_ERROR。
- 响应：返回本次关联或恢复的附件 ID 列表。
""",
)
@inject
async def add_resource_attachments(
    req: AddResourceAttachmentsRequest,
    user_id: str = Depends(require_login),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
    resource_client: ResourceClient = Depends(Provide[Container.resource_client]),
):
    session = await session_repo.get_session_for_user(req.session_id, user_id)
    attachment_ids = []

    for resource_id in req.resource_ids:
        resource_info = await resource_client.get_resource_info(
            resource_id=resource_id,
            user_id=user_id,
            group_role_map=SecurityContextHolder.get_group_role_map(),
        )
        resource_type = resource_info.resource_type
        attachment_name = resource_info.resource_name

        # 已存在记录
        existing = next((ref for ref in session.resource_attachment_refs if ref.resource_id == resource_id), None)
        if existing is not None:
            existing.resource_type = resource_type
            existing.attachment_name = attachment_name
            existing.deleted = False
            attachment_ids.append(existing.attachment_id)
            continue

        # 构建 attachment_id
        attachment_id = uuid.uuid4().hex

        session.resource_attachment_refs.append(
            ResourceAttachmentRef(
                attachment_id=attachment_id,
                attachment_name=attachment_name,
                resource_id=resource_id,
                resource_type=resource_type,
            )
        )
        attachment_ids.append(attachment_id)

    session.updated_at = datetime.now(timezone.utc)
    await session.save()

    info("resource attachments added", user_id=user_id, count=len(req.resource_ids))
    return R.success(data=attachment_ids)


@router.post(
    "/deleteAttachment",
    response_model=R,
    summary="删除会话附件",
    description="""
- 用途：从当前会话中删除临时附件或资源附件。
- 请求：session_id 指定目标会话；attachment_id 指定会话内附件。
- 约束：当前用户必须已登录；目标会话必须属于当前用户。
- 处理：匹配临时附件时标记删除并调用文件存储服务删除对象；匹配资源附件时仅标记删除；找不到未删除附件时跳过并返回成功。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；请求参数校验失败 -> ResultCode.PARAM_ERROR；会话不存在或不属于当前用户 -> ChatErrorCode.SESSION_NOT_FOUND；文件存储服务调用失败 -> ResultCode.SYSTEM_ERROR。
- 响应：成功时返回空结果。
""",
)
@inject
async def delete_attachment(
    req: DeleteAttachmentRequest,
    user_id: str = Depends(require_login),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
    file_storage_client: FileStorageClient = Depends(Provide[Container.file_storage_client])
):
    session = await session_repo.get_session_for_user(req.session_id, user_id)
    matched_temporary_attachment_ref = next((a for a in session.temporary_attachment_refs if a.attachment_id == req.attachment_id and not a.deleted), None)
    matched_resource_attachment_ref = next((a for a in session.resource_attachment_refs if a.attachment_id == req.attachment_id and not a.deleted), None)

    if matched_temporary_attachment_ref is None and matched_resource_attachment_ref is None:
        warning("attachment delete skipped", user_id=user_id, attachment_id=req.attachment_id)
        return R.success()

    if matched_temporary_attachment_ref is not None:
        matched_temporary_attachment_ref.deleted = True
        await file_storage_client.delete_file(matched_temporary_attachment_ref.object_key)

    if matched_resource_attachment_ref is not None:
        matched_resource_attachment_ref.deleted = True

    session.updated_at = datetime.now(timezone.utc)
    await session.save()

    info("attachment delete succeeded", user_id=user_id, attachment_id=req.attachment_id)
    return R.success()

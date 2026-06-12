from fastapi import APIRouter, Depends
from common.security.dependencies import require_login
from chat.application.attachment_service import AttachmentService
from chat.api.schemas.attachment import (
    InitUploadRequest,
    InitUploadResponse,
    ConfirmUploadRequest,
    DeleteAttachmentRequest,
    DeleteAttachmentResponse,
)
from chat.api.schemas.session import SessionResponse
from chat.container import container


router = APIRouter(tags=["attachment"])


@router.post("/initUpload", response_model=InitUploadResponse)
async def init_upload(
    req: InitUploadRequest,
    user_id: str = Depends(require_login),
):
    """附件直传初始化：返回 OSS 预签名 URL。enable_library=true 时走文档库路径，false 时走纯附件路径"""
    service: AttachmentService = container.attachment_service()
    return await service.init_upload(user_id, req)


@router.post("/confirmUpload", response_model=SessionResponse)
async def confirm_upload(
    req: ConfirmUploadRequest,
    user_id: str = Depends(require_login),
):
    """确认附件上传完成：从 OSS 下载到沙箱"""
    service: AttachmentService = container.attachment_service()
    session = await service.confirm_upload(user_id, req.session_id, req.object_key)
    return SessionResponse.from_entity(session)


@router.post("/delete", response_model=DeleteAttachmentResponse)
async def delete_attachment(
    req: DeleteAttachmentRequest,
    user_id: str = Depends(require_login),
):
    service: AttachmentService = container.attachment_service()
    return await service.delete_attachment(user_id, req.session_id, req.filename)

from fastapi import APIRouter, Depends
from common.security.dependencies import require_login
from chat.application.attachment_service import AttachmentService
from chat.api.schemas.attachment import (
    InitUploadRequest,
    InitUploadResponse,
    DeleteAttachmentRequest,
    DeleteAttachmentResponse,
)
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


@router.post("/delete", response_model=DeleteAttachmentResponse)
async def delete_attachment(
    req: DeleteAttachmentRequest,
    user_id: str = Depends(require_login),
):
    service: AttachmentService = container.attachment_service()
    return await service.delete_attachment(user_id, req.session_id, req.filename)

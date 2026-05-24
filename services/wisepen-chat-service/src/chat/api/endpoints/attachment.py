from fastapi import APIRouter, Depends
from common.security.dependencies import require_login
from chat.application.attachment_service import AttachmentService
from chat.api.schemas.attachment import (
    InitAttachmentUploadRequest,
    InitAttachmentUploadResponse,
    ConfirmUploadRequest,
    ConfirmUploadResponse,
    DeleteAttachmentRequest,
    DeleteAttachmentResponse,
    GetAttachmentPreviewUrlResponse,
)
from chat.container import container


router = APIRouter(tags=["attachment"])


@router.post("/initUpload", response_model=InitAttachmentUploadResponse)
async def init_upload(
    req: InitAttachmentUploadRequest,
    user_id: str = Depends(require_login),
):
    """初始化上传附件：前端点击附件按钮后调用，申请上传凭证并预写状态为pending的文件记录"""
    service: AttachmentService = container.attachment_service()
    return await service.init_upload(user_id, req)


@router.post("/confirmUpload", response_model=ConfirmUploadResponse)
async def confirm_upload(
    req: ConfirmUploadRequest,
    _=Depends(require_login),
):
    """确认上传附件：前端直传 OSS 成功后回调，将附件状态从 pending 转为 uploaded"""
    service: AttachmentService = container.attachment_service()
    return await service.confirm_upload(req.object_key)



@router.post("/delete", response_model=DeleteAttachmentResponse)
async def delete_attachment(
    req: DeleteAttachmentRequest,
    _=Depends(require_login),
):
    service: AttachmentService = container.attachment_service()
    return await service.delete_attachment(req.object_key)


@router.get("/preview", response_model=GetAttachmentPreviewUrlResponse)
async def preview_attachment(
    object_key: str,
    duration_seconds: int = 900,
    _=Depends(require_login),
):
    service: AttachmentService = container.attachment_service()
    return await service.get_preview_url(object_key, duration_seconds)

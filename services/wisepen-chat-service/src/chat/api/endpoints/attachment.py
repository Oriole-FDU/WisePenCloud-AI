from fastapi import APIRouter, Depends, UploadFile, File, Form
from common.security.dependencies import require_login
from chat.application.attachment_service import AttachmentService
from chat.api.schemas.attachment import (
    InitLargeUploadRequest,
    InitLargeUploadResponse,
    UploadSmallResponse,
    DeleteAttachmentRequest,
    DeleteAttachmentResponse,
    GetAttachmentPreviewUrlResponse,
)
from chat.container import container


router = APIRouter(tags=["attachment"])


@router.post("/initLargeUpload", response_model=InitLargeUploadResponse)
async def init_large_upload(
    req: InitLargeUploadRequest,
    user_id: str = Depends(require_login),
):
    """初始化大文件上传：返回 OSS 预签名 URL 供前端直传"""
    service: AttachmentService = container.attachment_service()
    return await service.init_large_upload(user_id, req)


@router.post("/uploadSmall", response_model=UploadSmallResponse)
async def upload_small(
    session_id: str = Form(...),
    file: UploadFile = File(...),
    user_id: str = Depends(require_login),
):
    """小文件上传：服务端中转 PUT 到 OSS"""
    service: AttachmentService = container.attachment_service()
    return await service.upload_small_file(user_id, session_id, file)


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

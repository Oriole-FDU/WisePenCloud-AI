from dependency_injector.wiring import inject, Provide
from fastapi import APIRouter, BackgroundTasks, Depends

from chat.api.schemas.attachment import (
    CheckAttachmentUploadCapabilityRequest,
    CheckAttachmentUploadCapabilityResponse,
    CompleteAttachmentUploadRequest,
    CompleteAttachmentUploadResponse,
    InitAttachmentUploadRequest,
    InitAttachmentUploadResponse,
    QueryAttachmentRequest,
    QueryAttachmentResponse,
    RemoveAttachmentRequest,
    RemoveAttachmentResponse,
    ReportAttachmentParseResultRequest,
    ReportAttachmentParseResultResponse,
    UpdateAttachmentConfigRequest,
    UpdateAttachmentConfigResponse,
)
from chat.application.attachment_service import AttachmentService
from chat.container import Container
from common.core.domain import R
from common.security import require_login

router = APIRouter()


@router.post("/initUpload", response_model=R[InitAttachmentUploadResponse], status_code=200)
@inject
async def init_attachment_upload(
    req: InitAttachmentUploadRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(require_login),
    service: AttachmentService = Depends(Provide[Container.attachment_service]),
):
    data = await service.init_upload(req, user_id)
    if data.flash_uploaded:
        background_tasks.add_task(
            service.auto_parse_uploaded_attachment,
            data.attachment_id,
            req.session_id,
            user_id,
        )
    return R.success(data=data)


@router.post("/completeUpload", response_model=R[CompleteAttachmentUploadResponse], status_code=200)
@inject
async def complete_attachment_upload(
    req: CompleteAttachmentUploadRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(require_login),
    service: AttachmentService = Depends(Provide[Container.attachment_service]),
):
    data = await service.complete_upload(req, user_id)
    background_tasks.add_task(
        service.auto_parse_uploaded_attachment,
        req.attachment_id,
        req.session_id,
        user_id,
    )
    return R.success(data=data)


@router.post("/queryAttachments", response_model=R[QueryAttachmentResponse], status_code=200)
@inject
async def query_attachments(
    req: QueryAttachmentRequest,
    user_id: str = Depends(require_login),
    service: AttachmentService = Depends(Provide[Container.attachment_service]),
):
    data = await service.query_attachments(req, user_id)
    return R.success(data=data)


@router.post("/checkUploadCapability", response_model=R[CheckAttachmentUploadCapabilityResponse], status_code=200)
@inject
async def check_upload_capability(
    req: CheckAttachmentUploadCapabilityRequest,
    service: AttachmentService = Depends(Provide[Container.attachment_service]),
):
    data = await service.check_upload_capability(req)
    return R.success(data=data)


@router.post("/updateAttachmentConfig", response_model=R[UpdateAttachmentConfigResponse], status_code=200)
@inject
async def update_attachment_config(
    req: UpdateAttachmentConfigRequest,
    user_id: str = Depends(require_login),
    service: AttachmentService = Depends(Provide[Container.attachment_service]),
):
    data = await service.update_attachment_config(req, user_id)
    return R.success(data=data)


@router.post("/reportParseResult", response_model=R[ReportAttachmentParseResultResponse], status_code=200)
@inject
async def report_parse_result(
    req: ReportAttachmentParseResultRequest,
    user_id: str = Depends(require_login),
    service: AttachmentService = Depends(Provide[Container.attachment_service]),
):
    data = await service.report_parse_result(req, user_id)
    return R.success(data=data)


@router.post("/removeAttachment", response_model=R[RemoveAttachmentResponse], status_code=200)
@inject
async def remove_attachment(
    req: RemoveAttachmentRequest,
    user_id: str = Depends(require_login),
    service: AttachmentService = Depends(Provide[Container.attachment_service]),
):
    data = await service.remove_attachment(req, user_id)
    return R.success(data=data)

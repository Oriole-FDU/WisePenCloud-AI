import asyncio
import mimetypes
import re
import tempfile
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from chat.api.schemas.chat_file import UploadedChatFileResponse
from chat.application.api_service.document_export import DocumentExportDownloadService
from chat.application.infra.document_temp_files.path import (
    sanitize_document_filename,
    session_root_for,
)
from chat.application.tools.document.services.document_export.errors import ExportOutputError
from chat.container import Container
from chat.domain.repositories import SessionRepository
from common.core.domain import R
from common.logger import log_event
from common.security import require_login

router = APIRouter()

_UPLOAD_ROOT = Path(tempfile.gettempdir()) / "wisepen-chat-upload-files"
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@router.post("/uploadChatFile", response_model=R[UploadedChatFileResponse], status_code=200)
@inject
async def upload_chat_file(
    request: Request,
    session_id: str = Query(..., min_length=1),
    file_name: str = Query(..., min_length=1),
    user_id: str = Depends(require_login),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    """上传当前会话的临时文件。"""
    await session_repo.get_by_id_and_user(session_id, user_id)

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="Uploaded file is too large.",
                )
        except ValueError:
            pass

    safe_file_name = sanitize_document_filename(file_name, default="upload.bin")
    file_id = uuid.uuid4().hex

    target_dir = session_root_for(
        temp_root=_UPLOAD_ROOT,
        user_id=user_id,
        session_id=session_id,
    )
    await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)

    target_path = target_dir / f"{file_id}-{safe_file_name}"
    tmp_path = target_dir / f".{file_id}.tmp"
    size = 0

    try:
        with tmp_path.open("wb") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue

                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Uploaded file is too large.",
                    )

                await asyncio.to_thread(handle.write, chunk)

        if size <= 0:
            raise HTTPException(
                status_code=400,
                detail="Uploaded file is empty.",
            )

        await asyncio.to_thread(tmp_path.replace, target_path)
    finally:
        await asyncio.to_thread(tmp_path.unlink, missing_ok=True)

    content_type = _content_type(
        explicit=request.headers.get("content-type"),
        file_name=safe_file_name,
    )
    preview_url = (
        "/chat/chatFile/previewChatFile"
        f"?session_id={quote(session_id)}&file_id={quote(file_id)}"
    )
    download_url = (
        "/chat/chatFile/downloadChatFile"
        f"?session_id={quote(session_id)}&file_id={quote(file_id)}"
    )

    log_event(
        "chat file uploaded",
        session_id=session_id,
        file_id=file_id,
        file_name=safe_file_name,
        content_type=content_type,
        size_bytes=size,
    )

    return R.success(
        data=UploadedChatFileResponse(
            file_id=file_id,
            file_ref=str(target_path.resolve(strict=False)),
            file_name=safe_file_name,
            content_type=content_type,
            size_bytes=size,
            preview_url=preview_url,
            download_url=download_url,
        )
    )


@router.get("/previewChatFile")
@inject
async def preview_chat_file(
    session_id: str = Query(..., min_length=1),
    file_id: str = Query(..., min_length=1),
    user_id: str = Depends(require_login),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    """预览当前会话上传的临时文件。"""
    await session_repo.get_by_id_and_user(session_id, user_id)

    file_path = _resolve_uploaded_file(
        user_id=user_id,
        session_id=session_id,
        file_id=file_id,
    )
    if file_path is None:
        raise HTTPException(
            status_code=404,
            detail="Uploaded file not found.",
        )

    filename = _filename_without_id(file_path.name, file_id=file_id)
    return FileResponse(
        path=file_path,
        media_type=_content_type(explicit=None, file_name=filename),
        filename=filename,
        content_disposition_type="inline",
    )


@router.get("/downloadChatFile")
@inject
async def download_chat_file(
    session_id: str = Query(..., min_length=1),
    file_id: str = Query(..., min_length=1),
    user_id: str = Depends(require_login),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    """下载当前会话上传的临时文件。"""
    await session_repo.get_by_id_and_user(session_id, user_id)

    file_path = _resolve_uploaded_file(
        user_id=user_id,
        session_id=session_id,
        file_id=file_id,
    )
    if file_path is None:
        raise HTTPException(
            status_code=404,
            detail="Uploaded file not found.",
        )

    filename = _filename_without_id(file_path.name, file_id=file_id)
    return FileResponse(
        path=file_path,
        media_type=_content_type(explicit=None, file_name=filename),
        filename=filename,
        content_disposition_type="attachment",
    )


@router.get("/previewGeneratedFile")
@inject
async def preview_generated_file(
    download_ref: str = Query(..., min_length=1),
    user_id: str = Depends(require_login),
    download_service: DocumentExportDownloadService = Depends(
        Provide[Container.document_export_download_service]
    ),
):
    """预览工具生成的临时文件。"""
    try:
        resolved = await download_service.resolve_existing_file(
            download_ref=download_ref,
            user_id=user_id,
        )
    except ExportOutputError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return FileResponse(
        path=resolved.file_path,
        media_type=resolved.content_type,
        filename=resolved.file_name,
        content_disposition_type="inline",
    )


@router.get("/downloadGeneratedFile")
@inject
async def download_generated_file(
    download_ref: str = Query(..., min_length=1),
    user_id: str = Depends(require_login),
    download_service: DocumentExportDownloadService = Depends(
        Provide[Container.document_export_download_service]
    ),
):
    """下载工具生成的临时文件。"""
    try:
        resolved = await download_service.resolve_existing_file(
            download_ref=download_ref,
            user_id=user_id,
        )
    except ExportOutputError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return FileResponse(
        path=resolved.file_path,
        media_type=resolved.content_type,
        filename=resolved.file_name,
        content_disposition_type="attachment",
    )


def _resolve_uploaded_file(
    *,
    user_id: str,
    session_id: str,
    file_id: str,
) -> Optional[Path]:
    base_dir = session_root_for(
        temp_root=_UPLOAD_ROOT,
        user_id=user_id,
        session_id=session_id,
    ).resolve(strict=False)

    if not file_id or not re.fullmatch(r"[a-fA-F0-9]{32}", file_id):
        return None

    for candidate in base_dir.glob(f"{file_id}-*"):
        resolved = candidate.resolve(strict=False)

        try:
            resolved.relative_to(base_dir)
        except ValueError:
            continue

        if resolved.is_file():
            return resolved

    return None


def _filename_without_id(name: str, *, file_id: str) -> str:
    prefix = f"{file_id}-"
    if name.startswith(prefix):
        return name[len(prefix) :] or "upload.bin"

    return name


def _content_type(*, explicit: Optional[str], file_name: str) -> str:
    if explicit:
        return explicit.split(";", 1)[0].strip() or "application/octet-stream"

    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or "application/octet-stream"

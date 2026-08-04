from __future__ import annotations

from typing import Any

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from common.core.domain import R
from common.core.exceptions import ServiceException
from common.security import SecurityContextHolder, require_login
from rag.api.schemas.resources import (
    PageContentRequest,
    ResourceRequest,
    SectionContentRequest,
)
from rag.application.rag.resource_snapshot import (
    RagPageContentRequest,
    RagResourceSnapshotNotFoundError,
    RagResourceSnapshotService,
    RagSectionContentRequest,
)
from rag.application.rag.retrieval import RagPermissionScope
from rag.container import Container
from rag.domain.error_codes import RagErrorCode

router = APIRouter()


@router.post("/document-structure", response_model=R[dict[str, Any]])
@inject
async def document_structure(
    request: ResourceRequest,
    user_id: str = Depends(require_login),
    service: RagResourceSnapshotService = Depends(
        Provide[Container.resource_snapshot_service]
    ),
) -> R[dict[str, Any]]:
    try:
        result = await service.snapshot(
            resource_id=request.resource_id,
            scope=_permission_scope(user_id),
        )
    except RagResourceSnapshotNotFoundError as error:
        raise ServiceException(RagErrorCode.NAVIGATION_STATE_NOT_FOUND) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.NAVIGATION_FAILED, str(error)) from error
    return R.success(_snapshot_payload(result))


@router.post("/page-content", response_model=R[dict[str, Any]])
@inject
async def read_page_content(
    request: PageContentRequest,
    user_id: str = Depends(require_login),
    service: RagResourceSnapshotService = Depends(
        Provide[Container.resource_snapshot_service]
    ),
) -> R[dict[str, Any]]:
    try:
        result = await service.read_pages(
            request=RagPageContentRequest(
                resource_id=request.resource_id,
                page_labels=request.page_labels,
            ),
            scope=_permission_scope(user_id),
        )
    except RagResourceSnapshotNotFoundError as error:
        raise ServiceException(RagErrorCode.NAVIGATION_STATE_NOT_FOUND) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.NAVIGATION_FAILED, str(error)) from error
    return R.success(_content_payload(result))


@router.post("/section-content", response_model=R[dict[str, Any]])
@inject
async def read_section_content(
    request: SectionContentRequest,
    user_id: str = Depends(require_login),
    service: RagResourceSnapshotService = Depends(
        Provide[Container.resource_snapshot_service]
    ),
) -> R[dict[str, Any]]:
    try:
        result = await service.read_sections(
            request=RagSectionContentRequest(
                resource_id=request.resource_id,
                section_ids=request.section_ids,
            ),
            scope=_permission_scope(user_id),
        )
    except RagResourceSnapshotNotFoundError as error:
        raise ServiceException(RagErrorCode.NAVIGATION_STATE_NOT_FOUND) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.NAVIGATION_FAILED, str(error)) from error
    return R.success(_content_payload(result))


def _permission_scope(user_id: str) -> RagPermissionScope:
    return RagPermissionScope(
        user_id=user_id,
        group_role_map=SecurityContextHolder.get_group_role_map(),
    )


def _snapshot_payload(result) -> dict[str, Any]:
    return {
        "resource_id": result.resource_id,
        "document_version": result.document_version,
        "content_revision": result.content_revision,
        "total_length": result.total_length,
        "pages": [
            {
                "page_label": page.page_label,
            }
            for page in result.pages
        ],
        "sections": [_section_payload(section) for section in result.sections],
    }


def _content_payload(result) -> dict[str, Any]:
    return {
        "resource_id": result.resource_id,
        "content_revision": result.content_revision,
        "document_version": result.document_version,
        "items": [_content_item_payload(item) for item in result.items],
    }


def _section_payload(section) -> dict[str, Any]:
    return {
        "section_id": section.section_id,
        "title": section.title,
        "level": section.level,
        "section_path": list(section.section_path),
        "has_content": section.has_content,
        "children": [_section_payload(child) for child in section.children],
    }


def _content_item_payload(item) -> dict[str, Any]:
    return {
        "key": item.key,
        "kind": item.kind,
        "reason": item.reason,
        "windows": [_window_payload(window) for window in item.windows],
    }


def _window_payload(window) -> dict[str, Any]:
    return {
        "text": window.text,
        "start_offset": window.start_offset,
        "end_offset": window.end_offset,
        "source_spans": [
            {
                "start_offset": span.start_offset,
                "end_offset": span.end_offset,
            }
            for span in window.source_spans
        ],
        "page_labels": list(window.page_labels),
        "section_paths": [list(path) for path in window.section_paths],
        "anchor_labels": list(window.anchor_labels),
        "metadata": window.metadata,
    }

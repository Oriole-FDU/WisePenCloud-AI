from __future__ import annotations

from typing import Any

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from common.core.domain import R
from common.core.exceptions import ServiceException
from common.security import SecurityContextHolder, require_login
from rag.api.schemas.resources import ResourceContentRequest, ResourceRequest
from rag.application.rag.resource_snapshot import (
    RagResourceContentRequest,
    RagResourceSnapshotNotFoundError,
    RagResourceSnapshotService,
)
from rag.application.rag.retrieval import RagPermissionScope
from rag.container import Container
from rag.domain.error_codes import RagErrorCode

router = APIRouter()


@router.post("/snapshot", response_model=R[dict[str, Any]])
@inject
async def snapshot(
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


@router.post("/content", response_model=R[dict[str, Any]])
@inject
async def read_content(
    request: ResourceContentRequest,
    user_id: str = Depends(require_login),
    service: RagResourceSnapshotService = Depends(
        Provide[Container.resource_snapshot_service]
    ),
) -> R[dict[str, Any]]:
    try:
        result = await service.read(
            request=RagResourceContentRequest(
                resource_id=request.resource_id,
                locator_name=request.locator_name,
                start=request.start,
                end=request.end,
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
        "locators": [
            {
                "locator_index": locator.locator_index,
                "name": locator.name,
                "kind": locator.kind.value,
                "start_offset": locator.start_offset,
                "end_offset": locator.end_offset,
                "section_path": _locator_section_path(locator.name),
            }
            for locator in result.locators
        ],
    }


def _content_payload(result) -> dict[str, Any]:
    return {
        "resource_id": result.resource_id,
        "content_revision": result.content_revision,
        "document_version": result.document_version,
        "locator_name": result.locator_name,
        "reason": result.reason,
        "windows": [
            {
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
                "metadata": window.metadata,
            }
            for window in result.windows
        ],
    }


def _locator_section_path(locator_name: str) -> list[str]:
    if not locator_name.startswith("section:"):
        return []
    return locator_name.removeprefix("section:").split(" > ")

"""将资源结构和正文读取暴露为内部 HTTP endpoints。"""

from typing import Annotated

from common.core.domain import R
from common.core.exceptions import ServiceException
from common.security import SecurityContextHolder, require_login
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from rag.api.schemas import (
    DocumentOutlineResponse,
    PageContentRequest,
    PageContentResponse,
    ResourceRequest,
    SectionContentRequest,
    SectionContentResponse,
)
from rag.application.rag.read import (
    ContentAccessRevokedError,
    ContentNotFoundError,
    DocumentContentReader,
    DocumentOutlineReader,
)
from rag.domain.error_codes import RagErrorCode
from rag.domain.models.acl import PermissionScope

router = APIRouter()

AuthenticatedUser = Annotated[str, Depends(require_login)]
OutlineReader = Annotated[
    DocumentOutlineReader,
    Depends(Provide["document_outline_reader"]),
]
ContentReader = Annotated[
    DocumentContentReader,
    Depends(Provide["document_content_reader"]),
]


@router.post(
    "/getDocumentOutline",
    response_model=R[DocumentOutlineResponse],
    response_model_exclude_none=True,
)
@inject
async def get_document_outline(
        request: ResourceRequest,
        user_id: AuthenticatedUser,
        reader: OutlineReader,
) -> R[DocumentOutlineResponse]:
    try:
        result = await reader.get_document_outline(
            resource_id=request.resource_id,
            permission_scope=_permission_scope(user_id),
        )
    except ContentNotFoundError as error:
        raise ServiceException(RagErrorCode.RESOURCE_CONTENT_NOT_FOUND) from error
    except ContentAccessRevokedError as error:
        raise ServiceException(RagErrorCode.RESOURCE_READ_FAILED) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.RESOURCE_READ_FAILED) from error
    return R.success(
        DocumentOutlineResponse(
            resource_id=result.resource_id,
            document_version=result.document_version,
            content_revision=result.content_revision,
            total_length=result.total_length,
            outline=result.outline,
        )
    )


@router.post(
    "/getPageContent",
    response_model=R[PageContentResponse],
    response_model_exclude_none=True,
)
@inject
async def get_page_content(
        request: PageContentRequest,
        user_id: AuthenticatedUser,
        reader: ContentReader,
) -> R[PageContentResponse]:
    try:
        result = await reader.get_pages(
            resource_id=request.resource_id,
            page_labels=request.page_labels,
            permission_scope=_permission_scope(user_id),
        )
    except ContentNotFoundError as error:
        raise ServiceException(RagErrorCode.RESOURCE_CONTENT_NOT_FOUND) from error
    except ContentAccessRevokedError as error:
        raise ServiceException(RagErrorCode.RESOURCE_READ_FAILED) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.RESOURCE_READ_FAILED) from error
    return R.success(result)


@router.post(
    "/getSectionContent",
    response_model=R[SectionContentResponse],
    response_model_exclude_none=True,
)
@inject
async def get_section_content(
        request: SectionContentRequest,
        user_id: AuthenticatedUser,
        reader: ContentReader,
) -> R[SectionContentResponse]:
    try:
        result = await reader.get_sections(
            resource_id=request.resource_id,
            section_ids=request.section_ids,
            permission_scope=_permission_scope(user_id),
            include_body=request.include_body,
            exclude_directions=request.exclude_directions,
        )
    except ContentNotFoundError as error:
        raise ServiceException(RagErrorCode.RESOURCE_CONTENT_NOT_FOUND) from error
    except ContentAccessRevokedError as error:
        raise ServiceException(RagErrorCode.RESOURCE_READ_FAILED) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.RESOURCE_READ_FAILED) from error
    return R.success(result)


def _permission_scope(user_id: str) -> PermissionScope:
    return PermissionScope.from_group_roles(
        user_id,
        SecurityContextHolder.get_group_role_map(),
    )

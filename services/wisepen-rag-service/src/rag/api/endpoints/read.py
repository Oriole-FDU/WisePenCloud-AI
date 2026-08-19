"""将资源结构和正文读取暴露为内部 HTTP endpoints。"""

from typing import Annotated

from common.core.domain import R
from common.core.exceptions import ServiceException
from common.security import SecurityContextHolder, require_login
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from rag.api.schemas import (
    DocumentOutlineRequest,
    DocumentOutlineResponse,
    ReadPagesRequest,
    ReadPagesResponse,
    ReadSectionsRequest,
    ReadSectionsResponse,
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
    request: DocumentOutlineRequest,
    user_id: AuthenticatedUser,
    reader: OutlineReader,
) -> R[DocumentOutlineResponse]:
    try:
        result = await reader.get_document_outline(
            resource_id=request.resource_id,
            permission_scope=_permission_scope(user_id),
            root_section_id=request.root_section_id,
            depth=request.depth,
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
    "/readPages",
    response_model=R[ReadPagesResponse],
    response_model_exclude_none=True,
)
@inject
async def read_pages(
    request: ReadPagesRequest,
    user_id: AuthenticatedUser,
    reader: ContentReader,
) -> R[ReadPagesResponse]:
    try:
        result = await reader.read_pages(
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
    "/readSections",
    response_model=R[ReadSectionsResponse],
    response_model_exclude_none=True,
)
@inject
async def read_sections(
    request: ReadSectionsRequest,
    user_id: AuthenticatedUser,
    reader: ContentReader,
) -> R[ReadSectionsResponse]:
    try:
        result = await reader.read_sections(
            resource_id=request.resource_id,
            section_ids=request.section_ids,
            permission_scope=_permission_scope(user_id),
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

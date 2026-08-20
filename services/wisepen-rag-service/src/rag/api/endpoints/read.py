"""将资源结构和正文读取暴露为内部 HTTP endpoints。"""

from typing import Annotated

from common.core.domain import R
from common.core.exceptions import ServiceException
from common.security import SecurityContextHolder, require_login
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from rag.api.schemas import (
    ReadPagesRequest,
    ReadPagesResponse,
    ReadSectionsRequest,
    ReadSectionsResponse,
    SurroundingOutlineRequest,
    SurroundingOutlineResponse,
)
from rag.application.rag.read import (
    ContentAccessRevokedError,
    ContentNotFoundError,
    DocumentContentReader,
    SectionNeighborhoodReader,
)
from rag.domain.error_codes import RagErrorCode
from rag.domain.models.acl import PermissionScope

router = APIRouter()

AuthenticatedUser = Annotated[str, Depends(require_login)]
ContentReader = Annotated[
    DocumentContentReader,
    Depends(Provide["document_content_reader"]),
]
NeighborhoodReader = Annotated[
    SectionNeighborhoodReader,
    Depends(Provide["section_neighborhood_reader"]),
]


@router.post(
    "/getSurroundingOutline",
    response_model=R[SurroundingOutlineResponse],
    response_model_exclude_none=True,
    response_model_exclude_defaults=True,
)
@inject
async def get_surrounding_outline(
    request: SurroundingOutlineRequest,
    user_id: AuthenticatedUser,
    reader: NeighborhoodReader,
) -> R[SurroundingOutlineResponse]:
    try:
        result = await reader.get_surrounding_outline(
            resource_id=request.resource_id,
            section_id=request.section_id,
            window_size=request.window_size,
            permission_scope=_permission_scope(user_id),
        )
    except ContentNotFoundError as error:
        raise ServiceException(RagErrorCode.RESOURCE_CONTENT_NOT_FOUND) from error
    except ContentAccessRevokedError as error:
        raise ServiceException(RagErrorCode.RESOURCE_READ_FAILED) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.RESOURCE_READ_FAILED) from error
    return R.success(
        SurroundingOutlineResponse.model_validate(result, from_attributes=True)
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

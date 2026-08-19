"""将 LOCATE 暴露为内部 HTTP endpoint。"""

from typing import Annotated

from common.core.domain import R
from common.core.exceptions import ServiceException
from common.security import SecurityContextHolder, require_login
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from rag.api.schemas import CandidateLocateRequest as LocateHttpRequest
from rag.api.schemas import CandidateLocateResponse
from rag.application.rag.navigate import (
    LocateError,
    ReadingCandidateLocator,
)
from rag.domain.error_codes import RagErrorCode
from rag.domain.models.acl import PermissionScope

router = APIRouter()

AuthenticatedUser = Annotated[str, Depends(require_login)]
Locator = Annotated[
    ReadingCandidateLocator,
    Depends(Provide["reading_entry_locator"]),
]


@router.post(
    "/locateCandidate",
    response_model=R[CandidateLocateResponse],
    response_model_exclude_none=True,
)
@inject
async def locate_candidate(
        request: LocateHttpRequest,
        user_id: AuthenticatedUser,
        locator: Locator,
) -> R[CandidateLocateResponse]:
    try:
        result = await locator.locate(
            session_id=request.session_id,
            semantic_query=request.semantic_query,
            lexical_query=request.lexical_query,
            max_results=request.max_results,
            permission_scope=_permission_scope(user_id),
        )
    except (LocateError, ValueError) as e:
        raise ServiceException(RagErrorCode.NAVIGATION_INVALID) from e
    except Exception as e:
        raise ServiceException(RagErrorCode.NAVIGATION_FAILED) from e
    return R.success(CandidateLocateResponse.model_validate(result))


def _permission_scope(user_id: str) -> PermissionScope:
    return PermissionScope.from_group_roles(
        user_id,
        SecurityContextHolder.get_group_role_map(),
    )

"""将 EXPAND 暴露为内部 HTTP endpoint。"""

from typing import Annotated

from common.core.domain import R
from common.core.exceptions import ServiceException
from common.security import SecurityContextHolder, require_login
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from rag.api.schemas import (
    GraphExpandRequest as ExpandHttpRequest,
)
from rag.api.schemas import (
    GraphExpandResponse,
    SectionChildrenExpandResponse,
    SectionExpandRequest,
    SectionExpandResponse,
)
from rag.application.rag.navigate import (
    EvidenceRevisionError,
    GraphAccessRevokedError,
    KnowledgeGraphExpander,
    NavigationStateNotFoundError,
    SectionExpander,
    UnknownSeedNodeError,
)
from rag.application.rag.read import ContentAccessRevokedError, ContentNotFoundError
from rag.domain.error_codes import RagErrorCode
from rag.domain.models.acl import PermissionScope

router = APIRouter()

AuthenticatedUser = Annotated[str, Depends(require_login)]
GraphExpander = Annotated[
    KnowledgeGraphExpander,
    Depends(Provide["knowledge_graph_expander"]),
]
SectionTreeExpander = Annotated[
    SectionExpander,
    Depends(Provide["section_expander"]),
]


@router.post("/expandGraph", response_model=R[GraphExpandResponse])
@inject
async def expand_graph(
    request: ExpandHttpRequest,
    user_id: AuthenticatedUser,
    expander: GraphExpander,
) -> R[GraphExpandResponse]:
    try:
        result = await expander.expand(
            state_id=request.state_id,
            session_id=request.session_id,
            permission_scope=_permission_scope(user_id),
            seed_node_ids=request.seed_node_ids,
            relation_types=request.relation_types,
            direction=request.direction,
            max_depth=request.max_depth,
            max_results=request.max_results,
            query=request.query,
        )
    except NavigationStateNotFoundError as error:
        raise ServiceException(RagErrorCode.NAVIGATION_STATE_NOT_FOUND) from error
    except EvidenceRevisionError as error:
        raise ServiceException(RagErrorCode.NAVIGATION_STATE_INVALIDATED) from error
    except GraphAccessRevokedError as error:
        raise ServiceException(RagErrorCode.NAVIGATION_STATE_INVALIDATED) from error
    except (UnknownSeedNodeError, ValueError) as error:
        raise ServiceException(RagErrorCode.NAVIGATION_INVALID) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.NAVIGATION_FAILED) from error
    return R.success(
        GraphExpandResponse(
            state_id=result.state_id,
            traversal_direction=result.traversal_direction,
            seed_nodes=result.seed_nodes,
            discovered_nodes=result.discovered_nodes,
            paths=result.paths,
            evidence_reading_blocks=result.evidence_reading_blocks,
        )
    )


def _permission_scope(user_id: str) -> PermissionScope:
    return PermissionScope.from_group_roles(
        user_id,
        SecurityContextHolder.get_group_role_map(),
    )


@router.post(
    "/expandSection",
    response_model=R[SectionExpandResponse | SectionChildrenExpandResponse],
)
@inject
async def expand_section(
    request: SectionExpandRequest,
    user_id: AuthenticatedUser,
    expander: SectionTreeExpander,
) -> R[SectionExpandResponse]:
    try:
        result = await expander.expand(
            resource_id=request.resource_id,
            section_id=request.section_id,
            direction=request.direction,
            permission_scope=_permission_scope(user_id),
            char_budget=request.char_budget,
            after_section_id=request.after_section_id,
        )
    except ContentNotFoundError as error:
        raise ServiceException(RagErrorCode.RESOURCE_CONTENT_NOT_FOUND) from error
    except ContentAccessRevokedError as error:
        raise ServiceException(RagErrorCode.RESOURCE_READ_FAILED) from error
    except ValueError as error:
        raise ServiceException(RagErrorCode.NAVIGATION_INVALID) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.NAVIGATION_FAILED) from error
    if hasattr(result, "sections"):
        payload = SectionChildrenExpandResponse(
            from_section_id=result.from_section_id,
            sections=result.sections,
            has_more=result.has_more,
            next_after_section_id=result.next_after_section_id,
            budget_exhausted=result.budget_exhausted,
        )
    else:
        payload = SectionExpandResponse(
            from_section_id=result.from_section_id,
            section_id=result.section.section_id,
            title=result.section.title,
            section_path=result.section.section_path,
            text=result.section.text,
            allowed_directions=result.section.allowed_directions,
        )
    return R.success(payload)

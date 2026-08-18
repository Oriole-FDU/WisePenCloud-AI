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
)
from rag.application.rag.navigate import (
    EvidenceRevisionError,
    GraphAccessRevokedError,
    KnowledgeGraphExpander,
    NavigationStateNotFoundError,
    UnknownSeedNodeError,
)
from rag.domain.error_codes import RagErrorCode
from rag.domain.models.acl import PermissionScope

router = APIRouter()

AuthenticatedUser = Annotated[str, Depends(require_login)]
GraphExpander = Annotated[
    KnowledgeGraphExpander,
    Depends(Provide["knowledge_graph_expander"]),
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
            evidence_sections=result.evidence_sections,
        )
    )

def _permission_scope(user_id: str) -> PermissionScope:
    return PermissionScope.from_group_roles(
        user_id,
        SecurityContextHolder.get_group_role_map(),
    )

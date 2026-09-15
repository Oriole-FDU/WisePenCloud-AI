"""通用图谱检索 HTTP 适配；限制通用参数范围，不接收垂类过滤器。"""

from typing import Annotated

from common.core.domain import R, ResultCode
from common.core.exceptions import ServiceException
from common.security import require_login
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from rag.api.endpoints.common import permission_scope
from rag.api.schemas.graph import (
    GraphHitResponse,
    SearchGraphRequest,
    SearchGraphResponse,
)
from rag.application.retrieval.graph_retriever import GraphRetriever
from rag.application.retrieval.models import GraphSearchRequest
from rag.container import Container
from rag.domain.error_codes import RagErrorCode

router = APIRouter()
AuthenticatedUser = Annotated[str, Depends(require_login)]
Retriever = Annotated[GraphRetriever, Depends(Provide[Container.graph_retriever])]


@router.post("/searchGraph", response_model=R[SearchGraphResponse], response_model_exclude_none=True)
@inject
async def search_graph(
    request: SearchGraphRequest,
    user_id: AuthenticatedUser,
    retriever: Retriever,
) -> R[SearchGraphResponse]:
    try:
        result = await retriever.search(
            GraphSearchRequest(
                query=request.query,
                level=request.level,
                seed_node_ids=list(request.seed_node_ids),
                resource_ids=list(request.resource_ids) if request.resource_ids else None,
                node_categories=list(request.node_categories),
                relation_types=list(request.relation_types),
                direction=request.direction,
                max_depth=request.max_depth,
                vector_top_n=request.vector_top_n,
                candidate_limit=request.candidate_limit,
                top_k=request.top_k,
            ),
            scope=permission_scope(user_id),
        )
    except ValueError as error:
        raise ServiceException(ResultCode.PARAM_ERROR, str(error)) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.QUERY_FAILED) from error

    return R.success(
        SearchGraphResponse(
            relevance_decision=result.relevance_decision,
            hits=[GraphHitResponse.model_validate(item) for item in result.hits],
        )
    )

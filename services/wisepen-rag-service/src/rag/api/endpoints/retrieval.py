"""混合检索的 HTTP 传输适配。"""

from typing import Annotated

from common.core.domain import R, ResultCode
from common.core.exceptions import ServiceException
from common.security import require_login
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from rag.api.endpoints.common import permission_scope
from rag.api.schemas.retrieval import (
    DynamicParentResponse,
    SearchHybridRequest,
    SearchHybridResponse,
)
from rag.application.retrieval.hybrid_retriever import HybridRetriever
from rag.container import Container
from rag.domain.error_codes import RagErrorCode

router = APIRouter()

AuthenticatedUser = Annotated[str, Depends(require_login)]
Retriever = Annotated[
    HybridRetriever,
    Depends(Provide[Container.hybrid_retriever]),
]


@router.post(
    "/searchHybrid",
    response_model=R[SearchHybridResponse],
    response_model_exclude_none=True,
    summary="混合检索",
)
@inject
async def search_hybrid(
    request: SearchHybridRequest,
    user_id: AuthenticatedUser,
    retriever: Retriever,
) -> R[SearchHybridResponse]:
    """执行单次文档混合检索，不隐式进入图谱或读取流程。"""
    try:
        result = await retriever.retrieve(
            request.semantic_query,
            request.top_k,
            lexical_query=request.lexical_query,
            scope=permission_scope(user_id),
        )
    except ValueError as error:
        # API schema 无法表达的执行参数错误仍是调用方参数错误。
        raise ServiceException(ResultCode.PARAM_ERROR, str(error)) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.QUERY_FAILED) from error

    return R.success(
        SearchHybridResponse(
            relevance_decision=result.relevance_decision,
            parents=[
                DynamicParentResponse(
                    resource_id=item.resource_id,
                    section_id=item.section_id,
                    section_path=" > ".join(item.section_path),
                    text=item.text,
                    score=item.score,
                )
                for item in result.parents
            ],
        )
    )

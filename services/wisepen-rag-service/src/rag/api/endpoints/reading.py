"""文档阅读和标题导航的 HTTP 传输适配。"""

from typing import Annotated

from common.core.domain import R
from common.core.exceptions import ServiceException
from common.security import require_login
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from rag.api.endpoints.common import permission_scope
from rag.api.schemas.reading import (
    GetGlobalOutlineRequest,
    GetGlobalOutlineResponse,
    GetNeighborhoodRequest,
    GetNeighborhoodResponse,
    NeighborhoodResponse,
    ReadPageResponse,
    ReadPagesRequest,
    ReadPagesResponse,
    ReadSectionResponse,
    ReadSectionsRequest,
    ReadSectionsResponse,
)
from rag.application.outline import OutlineBuilder
from rag.application.reading import DocumentReader, DocumentReadError
from rag.container import Container
from rag.domain.error_codes import RagErrorCode

router = APIRouter()

AuthenticatedUser = Annotated[str, Depends(require_login)]
Reader = Annotated[DocumentReader, Depends(Provide[Container.document_reader])]
Outline = Annotated[OutlineBuilder, Depends(Provide[Container.outline_builder])]


@router.post("/readPages", response_model=R[ReadPagesResponse], summary="按页读取")
@inject
async def read_pages(
    request: ReadPagesRequest,
    user_id: AuthenticatedUser,
    reader: Reader,
) -> R[ReadPagesResponse]:
    """按调用方顺序返回真实页标对应的完整页面正文。"""
    try:
        pages = await reader.read_pages(
            request.resource_id,
            request.page_labels,
            scope=permission_scope(user_id),
        )
    except DocumentReadError as error:
        raise ServiceException(RagErrorCode.RESOURCE_NOT_VISIBLE) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.QUERY_FAILED) from error
    return R.success(
        ReadPagesResponse(
            resource_id=request.resource_id,
            pages=[ReadPageResponse.model_validate(item) for item in pages],
        )
    )


@router.post("/readSections", response_model=R[ReadSectionsResponse], summary="按章节读取")
@inject
async def read_sections(
    request: ReadSectionsRequest,
    user_id: AuthenticatedUser,
    reader: Reader,
) -> R[ReadSectionsResponse]:
    """读取全局 Section ID；递归模式由 application 保留原 Markdown 标题层级。"""
    try:
        sections = await reader.read_sections(
            request.section_ids,
            mode=request.mode,
            max_depth=request.max_depth,
            scope=permission_scope(user_id),
        )
    except DocumentReadError as error:
        raise ServiceException(RagErrorCode.RESOURCE_NOT_VISIBLE) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.QUERY_FAILED) from error
    return R.success(
        ReadSectionsResponse(
            sections=[ReadSectionResponse.model_validate(item) for item in sections]
        )
    )


@router.post(
    "/getNeighborhood",
    response_model=R[GetNeighborhoodResponse],
    summary="查询章节邻域目录",
)
@inject
async def get_neighborhood(
    request: GetNeighborhoodRequest,
    user_id: AuthenticatedUser,
    outline: Outline,
) -> R[GetNeighborhoodResponse]:
    """每个请求 Section 独立生成目录，不合并多个窗口。"""
    try:
        items = await outline.neighborhood(
            request.section_ids,
            sibling_steps=request.sibling_steps,
            scope=permission_scope(user_id),
        )
    except DocumentReadError as error:
        raise ServiceException(RagErrorCode.RESOURCE_NOT_VISIBLE) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.QUERY_FAILED) from error
    return R.success(
        GetNeighborhoodResponse(
            items=[NeighborhoodResponse.model_validate(item) for item in items]
        )
    )


@router.post(
    "/getGlobalOutline",
    response_model=R[GetGlobalOutlineResponse],
    summary="查询文档全局目录",
)
@inject
async def get_global_outline(
    request: GetGlobalOutlineRequest,
    user_id: AuthenticatedUser,
    outline: Outline,
) -> R[GetGlobalOutlineResponse]:
    """返回一个资源的 Markdown 目录；max_level=0 表示展开全部层级。"""
    try:
        content = await outline.global_outline(
            request.resource_id,
            max_level=request.max_level,
            scope=permission_scope(user_id),
        )
    except DocumentReadError as error:
        raise ServiceException(RagErrorCode.RESOURCE_NOT_VISIBLE) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.QUERY_FAILED) from error
    return R.success(
        GetGlobalOutlineResponse(resource_id=request.resource_id, outline=content)
    )

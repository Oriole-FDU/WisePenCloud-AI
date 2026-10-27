"""论文 HTTP 薄适配；业务路由与标识解析属于应用用例。"""

import asyncio
from typing import Annotated

from common.core.domain import R
from common.security import SecurityContextHolder
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Header

from scholar.api.schemas.papers import LookupRequest, SearchRequest
from scholar.application.lookup import LookupResult, PaperLookupService
from scholar.application.search import PaperSearchService, SearchResult
from scholar.container import Container
from scholar.domain.models.paper import PaperSource

router = APIRouter(prefix="/papers", tags=["papers"])


@router.post("/search", response_model=R[SearchResult])
@inject
async def search(
    request: SearchRequest,
    service: Annotated[
        PaperSearchService, Depends(Provide[Container.paper_search_service])
    ],
    timeout: Annotated[
        float, Depends(Provide[Container.config.SCHOLAR_REQUEST_TIMEOUT_SECONDS])
    ],
    x_openalex_api_key: Annotated[
        str | None, Header(alias="X-OpenAlex-Api-Key")
    ] = None,
) -> R[SearchResult]:
    async with asyncio.timeout(timeout):
        return R.success(
            await service.search(
                request.to_query(),
                credentials={PaperSource.OPENALEX: x_openalex_api_key}
                if x_openalex_api_key
                else {},
                user_id=SecurityContextHolder.get_user_id(),
            )
        )


@router.post("/lookup", response_model=R[LookupResult])
@inject
async def lookup(
    request: LookupRequest,
    service: Annotated[
        PaperLookupService, Depends(Provide[Container.paper_lookup_service])
    ],
    timeout: Annotated[
        float, Depends(Provide[Container.config.SCHOLAR_REQUEST_TIMEOUT_SECONDS])
    ],
    x_openalex_api_key: Annotated[
        str | None, Header(alias="X-OpenAlex-Api-Key")
    ] = None,
) -> R[LookupResult]:
    async with asyncio.timeout(timeout):
        return R.success(
            await service.lookup(request.to_query(), api_key=x_openalex_api_key)
        )

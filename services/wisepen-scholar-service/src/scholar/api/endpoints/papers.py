"""论文 HTTP 薄适配；业务路由与标识解析属于应用用例。"""

import asyncio

from common.core.domain import R
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from scholar.api.schemas.papers import LookupRequest, SearchRequest
from scholar.application.lookup import LookupResult, PaperLookupService
from scholar.application.search import PaperSearchService, SearchResult
from scholar.container import Container

router = APIRouter(prefix="/papers", tags=["papers"])


@router.post("/search", response_model=R[SearchResult])
@inject
async def search(
    request: SearchRequest,
    service: PaperSearchService = Depends(Provide[Container.paper_search_service]),
    timeout: float = Depends(Provide[Container.config.SCHOLAR_REQUEST_TIMEOUT_SECONDS]),
) -> R[SearchResult]:
    async with asyncio.timeout(timeout):
        return R.success(await service.search(request.to_query()))


@router.post("/lookup", response_model=R[LookupResult])
@inject
async def lookup(
    request: LookupRequest,
    service: PaperLookupService = Depends(Provide[Container.paper_lookup_service]),
    timeout: float = Depends(Provide[Container.config.SCHOLAR_REQUEST_TIMEOUT_SECONDS]),
) -> R[LookupResult]:
    async with asyncio.timeout(timeout):
        return R.success(await service.lookup(request.to_query()))

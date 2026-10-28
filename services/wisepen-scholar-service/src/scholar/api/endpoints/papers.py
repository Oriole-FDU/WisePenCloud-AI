"""论文 HTTP 薄适配；业务路由与标识解析属于应用用例。"""

import asyncio
from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Header

from common.core.domain import R
from common.security import SecurityContextHolder
from scholar.api.schemas.papers import DiscoveryRequest, LookupRequest, SearchRequest
from scholar.application.services.discovery import (
    DiscoveryResult,
    PaperDiscoveryService,
)
from scholar.application.services.lookup import LookupResult, PaperLookupService
from scholar.application.services.search import PaperSearchService, SearchResult
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
    x_huggingface_api_key: Annotated[
        str | None, Header(alias="X-HuggingFace-Api-Key")
    ] = None,
) -> R[SearchResult]:
    async with asyncio.timeout(timeout):
        return R.success(
            await service.search(
                request.to_query(),
                credentials={
                    key: value
                    for key, value in {
                        PaperSource.OPENALEX: x_openalex_api_key,
                        PaperSource.HUGGINGFACE: x_huggingface_api_key,
                    }.items()
                    if value
                },
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
    x_huggingface_api_key: Annotated[
        str | None, Header(alias="X-HuggingFace-Api-Key")
    ] = None,
) -> R[LookupResult]:
    async with asyncio.timeout(timeout):
        return R.success(
            await service.lookup(
                request.to_query(),
                credentials={
                    key: value
                    for key, value in {
                        PaperSource.OPENALEX: x_openalex_api_key,
                        PaperSource.HUGGINGFACE: x_huggingface_api_key,
                    }.items()
                    if value
                },
            )
        )


@router.post("/discover", response_model=R[DiscoveryResult])
@inject
async def discover(
    request: DiscoveryRequest,
    service: Annotated[
        PaperDiscoveryService, Depends(Provide[Container.paper_discovery_service])
    ],
    timeout: Annotated[
        float, Depends(Provide[Container.config.SCHOLAR_REQUEST_TIMEOUT_SECONDS])
    ],
    x_huggingface_api_key: Annotated[
        str | None, Header(alias="X-HuggingFace-Api-Key")
    ] = None,
) -> R[DiscoveryResult]:
    async with asyncio.timeout(timeout):
        return R.success(
            await service.discover(
                request.to_query(),
                credentials={PaperSource.HUGGINGFACE: x_huggingface_api_key}
                if x_huggingface_api_key
                else {},
            )
        )

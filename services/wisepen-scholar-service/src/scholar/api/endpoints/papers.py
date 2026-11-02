"""论文 HTTP 薄适配；业务路由与标识解析属于应用用例。"""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Header

from common.core.domain import R
from scholar.api.schemas.papers import DiscoveryRequest, LookupRequest, SearchRequest, MatchRequest
from scholar.application.match import MatchResult, PaperMatchService
from scholar.application.discovery import (
    DiscoveryResult,
    PaperDiscoveryService,
)
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
    x_huggingface_api_key: Annotated[
        str | None, Header(alias="X-HuggingFace-Api-Key")
    ] = None,
    x_s2_api_key: Annotated[str | None, Header(alias="X-S2-Api-Key")] = None,
) -> R[SearchResult]:
    # 搜索用例按阶段处理同一预算，富化超时保留检索结果。
    return R.success(
        await service.search(
            request.to_query(),
            credentials={
                key: value
                for key, value in {
                    PaperSource.OPENALEX: x_openalex_api_key,
                    PaperSource.HUGGINGFACE: x_huggingface_api_key,
                    PaperSource.S2: x_s2_api_key,
                }.items()
                if value
            },
            timeout=timeout,
        )
    )


@router.post("/match", response_model=R[MatchResult])
@inject
async def match(
    request: MatchRequest,
    service: Annotated[PaperMatchService, Depends(Provide[Container.paper_match_service])],
    timeout: Annotated[float, Depends(Provide[Container.config.SCHOLAR_REQUEST_TIMEOUT_SECONDS])],
    x_openalex_api_key: Annotated[str | None, Header(alias="X-OpenAlex-Api-Key")] = None,
    x_huggingface_api_key: Annotated[str | None, Header(alias="X-HuggingFace-Api-Key")] = None,
    x_s2_api_key: Annotated[str | None, Header(alias="X-S2-Api-Key")] = None,
) -> R[MatchResult]:
    # 用例管理逐项预算与失败，HTTP 不用整批 timeout 丢弃已完成项。
    return R.success(await service.match(request.to_query(), timeout=timeout, credentials={
        source: key for source, key in {
            PaperSource.OPENALEX: x_openalex_api_key,
            PaperSource.HUGGINGFACE: x_huggingface_api_key,
            PaperSource.S2: x_s2_api_key,
        }.items() if key
    }))


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
    x_s2_api_key: Annotated[str | None, Header(alias="X-S2-Api-Key")] = None,
) -> R[LookupResult]:
    # 用例分别限制读取和缓存写入，HTTP 不再因可选写入撤销已有结果。
    return R.success(
        await service.lookup(
            request.to_query(),
            credentials={
                key: value
                for key, value in {
                    PaperSource.OPENALEX: x_openalex_api_key,
                    PaperSource.HUGGINGFACE: x_huggingface_api_key,
                    PaperSource.S2: x_s2_api_key,
                }.items()
                if value
            },
            timeout=timeout,
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
    return R.success(
        await service.discover(
            request.to_query(),
            credentials={PaperSource.HUGGINGFACE: x_huggingface_api_key}
            if x_huggingface_api_key
            else {},
            timeout=timeout,
        )
    )

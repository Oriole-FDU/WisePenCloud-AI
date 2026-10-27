"""发现请求的 HTTP 适配；渠道选择与结果缓存属于发现用例。"""

import asyncio
from typing import Annotated

from common.core.domain import R
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from scholar.api.schemas.discovery import DiscoveryRequest
from scholar.application.discovery import DiscoveryResult, PaperDiscoveryService
from scholar.container import Container

router = APIRouter(prefix="/papers", tags=["papers"])


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
) -> R[DiscoveryResult]:
    async with asyncio.timeout(timeout):
        return R.success(await service.discover(request.to_query()))

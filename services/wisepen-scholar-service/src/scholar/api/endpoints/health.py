"""实时检查在线依赖及已发布索引，不以进程存活冒充就绪。"""

import asyncio

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from scholar.container import Container

router = APIRouter(tags=["health"])


@router.get("/health")
@inject
async def health(
    cache: Redis = Depends(Provide[Container.redis]),
    vectors: AsyncQdrantClient = Depends(Provide[Container.qdrant]),
    alias: str = Depends(Provide[Container.config.SCHOLAR_COLLECTION_ALIAS]),
    timeout: float = Depends(Provide[Container.config.SCHOLAR_REQUEST_TIMEOUT_SECONDS]),
):
    try:
        async with asyncio.timeout(timeout):
            await cache.ping()
            ready = await vectors.collection_exists(alias)
    except (RedisError, TimeoutError, ResponseHandlingException, UnexpectedResponse):
        ready = False
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready"},
    )

"""实时检查在线依赖及已发布索引，不以进程存活冒充就绪。"""

import asyncio

from dependency_injector.wiring import Provide, inject
from elastic_transport import TransportError
from elasticsearch import ApiError, AsyncElasticsearch
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError

from scholar.container import Container

router = APIRouter(tags=["health"])


@router.get("/health")
@inject
async def health(
    es: AsyncElasticsearch = Depends(Provide[Container.elasticsearch]),
    cache: Redis = Depends(Provide[Container.redis]),
    alias: str = Depends(Provide[Container.config.SCHOLAR_INDEX_ALIAS]),
    timeout: float = Depends(Provide[Container.config.SCHOLAR_REQUEST_TIMEOUT_SECONDS]),
):
    try:
        async with asyncio.timeout(timeout):
            await es.info()
            await cache.ping()
            ready = bool(await es.indices.exists_alias(name=alias))
    except (TransportError, ApiError, RedisError, TimeoutError):
        ready = False
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready"},
    )

"""RAG V3 HTTP 路由聚合。"""

from fastapi import APIRouter

from rag.api.endpoints import reading, retrieval
from rag.core.config.bootstrap_settings import bootstrap_settings

api_router = APIRouter()


@api_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": bootstrap_settings.SERVICE_NAME}


api_router.include_router(retrieval.router, prefix="/retrieval", tags=["retrieval"])
api_router.include_router(reading.router, prefix="/reading", tags=["reading"])

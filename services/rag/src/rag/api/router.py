"""ACL 阶段保留的健康检查路由。"""

from fastapi import APIRouter

from rag.core.config.bootstrap_settings import bootstrap_settings

api_router = APIRouter()


@api_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": bootstrap_settings.SERVICE_NAME}

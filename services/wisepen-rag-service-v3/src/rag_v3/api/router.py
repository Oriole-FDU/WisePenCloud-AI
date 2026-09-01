"""P0 仅暴露服务健康检查，不提前定义业务接口。"""

from fastapi import APIRouter

from rag_v3.core.config.bootstrap_settings import bootstrap_settings

api_router = APIRouter()


@api_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": bootstrap_settings.SERVICE_NAME}

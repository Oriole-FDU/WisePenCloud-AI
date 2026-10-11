"""ACL 阶段保留的健康检查路由。"""

from fastapi import APIRouter, Request, Response

from rag.core.config.bootstrap_settings import bootstrap_settings

api_router = APIRouter()


@api_router.get("/health")
async def health(request: Request, response: Response) -> dict[str, str]:
    ready = getattr(request.app.state, "ready", False)
    if not ready:
        response.status_code = 503
    return {
        "status": "ok" if ready else "not_ready",
        "service": bootstrap_settings.SERVICE_NAME,
    }

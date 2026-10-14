"""平台健康检查；服务身份由实际组合入口提供。"""

from fastapi import APIRouter, Request, Response

api_router = APIRouter()


@api_router.get("/health")
async def health(request: Request, response: Response) -> dict[str, str]:
    ready = request.app.state.ready
    if not ready:
        response.status_code = 503
    return {
        "status": "ok" if ready else "not_ready",
        "service": request.app.state.service_name,
    }

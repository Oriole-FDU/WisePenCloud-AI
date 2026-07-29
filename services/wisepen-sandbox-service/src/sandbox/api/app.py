from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from common.core.domain import R
from common.core.exceptions import ServiceException
from sandbox.gateway.api.router import create_gateway_router
from sandbox.gateway.binding import VncBinding

from sandbox.api.endpoints import health, pool, sandbox


def create_app(
    *,
    mcp_app: Any | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[Any]] | None = None,
    vnc_binding: VncBinding | None = None,
) -> FastAPI:
    app = FastAPI(
        title="WisePen Sandbox Service",
        version="0.2.0",
        description=(
            "WisePen 内部沙箱生命周期服务。提供预热池分配、租约执行、租约释放、"
            "状态查询和池指标接口；业务响应遵循 R(code/msg/data) 协议。"
        ),
        openapi_tags=[
            {"name": "health", "description": "进程存活与沙箱池就绪探针。"},
            {"name": "sandbox", "description": "沙箱租约生命周期和执行接口。"},
            {"name": "pool", "description": "预热池容量与运行指标接口。"},
        ],
        lifespan=lifespan,
    )

    @app.exception_handler(ServiceException)
    async def service_exception_handler(request: Request, exc: ServiceException):
        # 内部微服务协议统一返回 R(code/msg/data)，业务错误不使用 HTTP 4xx/5xx。
        return JSONResponse(
            status_code=200,
            content=R(code=exc.code, msg=exc.msg, data=None).model_dump(),
        )

    app.include_router(health.router)
    app.include_router(sandbox.router)
    app.include_router(pool.router)
    if mcp_app is not None:
        # 模型上下文协议使用 streamable-http，挂载后实际工具入口为 /mcp/。
        app.mount("/mcp", mcp_app)
    if vnc_binding is not None:
        app.include_router(create_gateway_router(vnc_binding))
    return app

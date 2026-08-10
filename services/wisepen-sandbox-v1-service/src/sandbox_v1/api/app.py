from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from common.core.domain import R
from common.core.exceptions import ServiceException
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from sandbox_v1.api.endpoints import health


def create_app(
    *,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[Any]] | None = None,
) -> FastAPI:

    app = FastAPI(
        title="WisePen Sandbox V1 Core Service",
        version="0.1.0",
        description="WisePen 内部沙箱 v1 核心生命周期服务。",
        openapi_tags=[
            {"name": "health", "description": "进程存活与沙箱池就绪探针。"},
            {"name": "pool", "description": "预热池容量与运行指标接口。"},
        ],
        lifespan=lifespan,
    )

    @app.exception_handler(ServiceException)
    async def service_exception_handler(request: Request, exc: ServiceException):
        return JSONResponse(
            status_code=200,
            content=R(code=exc.code, msg=exc.msg, data=None).model_dump(),
        )

    app.include_router(health.router)
    return app

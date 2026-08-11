from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from fastapi import FastAPI

from sandbox.api.endpoints import health


def create_app(
    *,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[Any]] | None = None,
) -> FastAPI:

    app = FastAPI(
        title="WisePen Sandbox Core Service",
        version="0.1.0",
        description="WisePen 内部沙箱核心生命周期服务。",
        openapi_tags=[
            {"name": "health", "description": "进程存活与沙箱池就绪探针。"},
            {"name": "pool", "description": "预热池容量与运行指标接口。"},
        ],
        lifespan=lifespan,
    )

    app.include_router(health.router)
    return app

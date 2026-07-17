from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from common.core.domain import R
from common.core.exceptions import ServiceException

from sandbox.api.routers.health import create_health_router
from sandbox.api.routers.pool import create_pool_router
from sandbox.api.routers.sandbox import create_sandbox_router
from sandbox.application.services.sandbox_pool import SandboxPool
from sandbox.application.services.sandbox_scheduler import SandboxScheduler


def create_app(scheduler: SandboxScheduler, pool: SandboxPool) -> FastAPI:
    app = FastAPI(title="WisePen Sandbox Service")

    @app.exception_handler(ServiceException)
    async def service_exception_handler(request: Request, exc: ServiceException):
        return JSONResponse(
            status_code=200,
            content=R(code=exc.code, msg=exc.msg, data=None).model_dump(),
        )

    app.include_router(create_health_router(pool))
    app.include_router(create_sandbox_router(scheduler))
    app.include_router(create_pool_router(pool))
    return app

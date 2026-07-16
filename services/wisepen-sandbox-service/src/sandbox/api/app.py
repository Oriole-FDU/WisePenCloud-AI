from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from common.core.domain import R

from sandbox.api.routers.health import create_health_router
from sandbox.api.routers.pool import create_pool_router
from sandbox.api.routers.sandbox import create_sandbox_router
from sandbox.domain.errors import SandboxDomainError
from sandbox.domain.error_codes import sandbox_error_code
from sandbox.application.services.sandbox_pool import SandboxPool
from sandbox.application.services.sandbox_scheduler import SandboxScheduler


def create_app(scheduler: SandboxScheduler, pool: SandboxPool) -> FastAPI:
    app = FastAPI(title="WisePen Sandbox Service")

    @app.exception_handler(SandboxDomainError)
    async def sandbox_domain_error_handler(request: Request, exc: SandboxDomainError):
        return JSONResponse(
            status_code=200,
            content=R.fail(
                sandbox_error_code(getattr(exc, "code", "SANDBOX_UNAVAILABLE")),
                custom_msg=str(exc) or None,
            ).model_dump(),
        )

    app.include_router(create_health_router(pool))
    app.include_router(create_sandbox_router(scheduler))
    app.include_router(create_pool_router(pool))
    return app

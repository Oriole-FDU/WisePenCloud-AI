from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from common.core.domain import R

from sandbox.error_codes import sandbox_error_code
from sandbox.errors import SandboxDomainError
from sandbox.queue_jurfal.models import ExecutionRequest, SandboxState
from sandbox.queue_jurfal.pool import SandboxPool
from sandbox.queue_jurfal.scheduler import SandboxScheduler


class AllocateBody(BaseModel):
    request_id: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    workspace_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class ExecuteBody(BaseModel):
    request_id: str = Field(min_length=1, max_length=200)
    tenant_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    workspace_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    fencing_token: int = Field(gt=0)
    operation: str = Field(min_length=1, max_length=50)
    payload: dict[str, Any] = Field(default_factory=dict)


class ReleaseBody(BaseModel):
    fencing_token: int = Field(gt=0)


def create_app(scheduler: SandboxScheduler, pool: SandboxPool) -> FastAPI:
    app = FastAPI(title="WisePen Sandbox Service")
    router = APIRouter(prefix="/internal")

    @app.exception_handler(SandboxDomainError)
    async def sandbox_domain_error_handler(request: Request, exc: SandboxDomainError):
        return JSONResponse(
            status_code=200,
            content=R.fail(
                sandbox_error_code(getattr(exc, "code", "SANDBOX_UNAVAILABLE")),
                custom_msg=str(exc) or None,
            ).model_dump(),
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, Any]:
        snapshot = await pool.snapshot()
        ready = snapshot.counts[SandboxState.READY]
        if ready < snapshot.min_ready:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "MIN_READY_NOT_REACHED",
                    "ready": ready,
                    "min_ready": snapshot.min_ready,
                },
            )
        return {"status": "ready", "ready": ready, "min_ready": snapshot.min_ready}

    @router.post("/sandboxes/allocate")
    async def allocate(body: AllocateBody) -> R[dict[str, Any]]:
        lease = await scheduler.allocate(body.request_id, body.tenant_id, body.workspace_id)
        return R.success(jsonable_encoder(asdict(lease)))

    @router.post("/leases/{lease_id}/execute")
    async def execute(lease_id: str, body: ExecuteBody) -> R[dict[str, Any]]:
        result = await scheduler.execute(
            lease_id,
            ExecutionRequest(
                request_id=body.request_id,
                tenant_id=body.tenant_id,
                workspace_id=body.workspace_id,
                operation=body.operation,
                payload=body.payload,
                fencing_token=body.fencing_token,
            ),
        )
        return R.success(jsonable_encoder(asdict(result)))

    @router.post("/leases/{lease_id}/release")
    async def release(lease_id: str, body: ReleaseBody) -> R[dict[str, str]]:
        await scheduler.release(lease_id, body.fencing_token)
        return R.success({"status": "released"})

    @router.get("/pool/metrics")
    async def metrics() -> R[dict[str, Any]]:
        return R.success((await pool.snapshot()).as_dict())

    @router.get("/sandboxes/{sandbox_id}")
    async def status(sandbox_id: str) -> R[dict[str, Any]]:
        result = asdict(await scheduler.status(sandbox_id))
        result.pop("provider_id", None)
        ref = result.get("ref")
        if isinstance(ref, dict):
            ref.pop("provider_id", None)
            ref.pop("metadata", None)
        endpoint = result.get("ref", {}).get("endpoint")
        if isinstance(endpoint, dict):
            endpoint["token"] = None
        return R.success(jsonable_encoder(result))

    app.include_router(router)
    return app

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from sandbox.errors import (
    LeaseExpiredError,
    LeaseNotFoundError,
    PoolEmptyError,
    SandboxDomainError,
    WorkspaceSyncError,
)
from sandbox.models import ExecutionRequest
from sandbox.pool import SandboxPool
from sandbox.scheduler import SandboxScheduler


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


def _error(exc: SandboxDomainError) -> HTTPException:
    status = 503
    if isinstance(exc, (LeaseNotFoundError, LeaseExpiredError)):
        status = 404 if isinstance(exc, LeaseNotFoundError) else 409
    elif exc.code in {"FENCING_REJECTED", "REQUEST_CONFLICT", "INVALID_STATE_TRANSITION"}:
        status = 409
    elif isinstance(exc, WorkspaceSyncError):
        status = 500
    return HTTPException(status_code=status, detail=exc.code)


def create_app(scheduler: SandboxScheduler, pool: SandboxPool) -> FastAPI:
    app = FastAPI(title="WisePen Sandbox Service")
    router = APIRouter(prefix="/internal")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/sandboxes/allocate")
    async def allocate(body: AllocateBody) -> dict[str, Any]:
        try:
            lease = await scheduler.allocate(body.request_id, body.tenant_id, body.workspace_id)
        except SandboxDomainError as exc:
            raise _error(exc) from exc
        return asdict(lease)

    @router.post("/leases/{lease_id}/execute")
    async def execute(lease_id: str, body: ExecuteBody) -> dict[str, Any]:
        try:
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
        except SandboxDomainError as exc:
            raise _error(exc) from exc
        return asdict(result)

    @router.post("/leases/{lease_id}/release")
    async def release(lease_id: str, body: ReleaseBody) -> dict[str, str]:
        try:
            await scheduler.release(lease_id, body.fencing_token)
        except SandboxDomainError as exc:
            raise _error(exc) from exc
        return {"status": "released"}

    @router.get("/pool/metrics")
    async def metrics() -> dict[str, Any]:
        return (await pool.snapshot()).as_dict()

    @router.get("/sandboxes/{sandbox_id}")
    async def status(sandbox_id: str) -> dict[str, Any]:
        try:
            result = asdict(await scheduler.status(sandbox_id))
            result.pop("provider_id", None)
            ref = result.get("ref")
            if isinstance(ref, dict):
                ref.pop("provider_id", None)
                ref.pop("metadata", None)
            endpoint = result.get("ref", {}).get("endpoint")
            if isinstance(endpoint, dict):
                endpoint["token"] = None
            return result
        except SandboxDomainError as exc:
            raise _error(exc) from exc

    app.include_router(router)
    return app

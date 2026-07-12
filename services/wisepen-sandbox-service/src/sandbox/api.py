from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from sandbox.errors import PoolEmptyError, SandboxDomainError
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
    operation: str = Field(min_length=1, max_length=50)
    payload: dict[str, Any] = Field(default_factory=dict)


def create_app(scheduler: SandboxScheduler, pool: SandboxPool) -> FastAPI:
    app = FastAPI(title="WisePen Sandbox Service")
    router = APIRouter(prefix="/internal")

    @router.post("/sandboxes/allocate")
    async def allocate(body: AllocateBody) -> dict[str, Any]:
        try:
            lease = await scheduler.allocate(
                body.request_id, body.tenant_id, body.workspace_id
            )
        except PoolEmptyError as exc:
            raise HTTPException(status_code=503, detail="POOL_EMPTY") from exc
        except SandboxDomainError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
                ),
            )
        except SandboxDomainError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return asdict(result)

    @router.post("/leases/{lease_id}/release")
    async def release(lease_id: str) -> dict[str, str]:
        try:
            await scheduler.release(lease_id)
        except SandboxDomainError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "released"}

    @router.get("/pool/metrics")
    async def metrics() -> dict[str, Any]:
        return await pool.snapshot()

    @router.get("/sandboxes/{sandbox_id}")
    async def status(sandbox_id: str) -> dict[str, Any]:
        try:
            return asdict(await scheduler.status(sandbox_id))
        except SandboxDomainError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    app.include_router(router)
    return app

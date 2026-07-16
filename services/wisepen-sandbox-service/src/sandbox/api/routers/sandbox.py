from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder

from common.core.domain import R
from sandbox.api.schemas import AllocateBody, ExecuteBody, ReleaseBody
from sandbox.application.services.sandbox_scheduler import SandboxScheduler
from sandbox.domain.entities import ExecutionRequest


def create_sandbox_router(scheduler: SandboxScheduler) -> APIRouter:
    router = APIRouter(prefix="/internal")

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

    return router

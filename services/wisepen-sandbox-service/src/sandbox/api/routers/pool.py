from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from common.core.domain import R
from sandbox.application.services.sandbox_pool import SandboxPool


def create_pool_router(pool: SandboxPool) -> APIRouter:
    router = APIRouter(prefix="/internal")

    @router.get("/pool/metrics")
    async def metrics() -> R[dict[str, Any]]:
        return R.success((await pool.snapshot()).as_dict())

    return router

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from sandbox.application.services.sandbox_pool import SandboxPool
from sandbox.domain.entities import SandboxState


def create_health_router(pool: SandboxPool) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/readyz")
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

    return router

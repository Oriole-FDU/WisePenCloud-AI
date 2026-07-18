from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from common.security.context import SecurityContextHolder
from sandbox.gateway.binding import VncBinding


def create_vnc_router(binding: VncBinding) -> APIRouter:
    router = APIRouter()

    def identity(request: Request) -> tuple[str, str]:
        user_id = (request.headers.get("X-User-Id") or SecurityContextHolder.get_user_id() or "").strip()
        session_id = (
            request.headers.get("X-Session-Id")
            or SecurityContextHolder.get_session_id()
            or ""
        ).strip()
        if not user_id or not session_id:
            raise HTTPException(status_code=400, detail="X-User-Id and X-Session-Id are required")
        return user_id, session_id

    @router.get("/vnc")
    async def vnc_page(request: Request) -> RedirectResponse:
        user_id, session_id = identity(request)
        connection = await binding.acquire(user_id, session_id)
        return RedirectResponse(connection.vnc_url, status_code=302)

    @router.post("/vnc/release")
    async def vnc_release(request: Request) -> dict[str, str]:
        user_id, session_id = identity(request)
        await binding.release(user_id, session_id)
        return {"status": "released"}

    @router.get("/vnc/status")
    async def vnc_status() -> dict[str, object]:
        return await binding.stats()

    return router

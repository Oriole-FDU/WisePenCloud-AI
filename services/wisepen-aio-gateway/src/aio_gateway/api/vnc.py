"""VNC — 302 redirect to user's sandbox noVNC frontend.

网关不感知 Docker/容器细节，仅通过 SandboxEndpoint 协议获取连接地址。
"""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from common.security.context import SecurityContextHolder
from aio_gateway.api import deps
from aio_gateway.sandbox_endpoint import SandboxEndpoint

router = APIRouter()


def _tenant(req: Request) -> tuple[str, str] | None:
    uid = (req.headers.get("X-User-Id") or
           SecurityContextHolder.get_user_id() or "").strip()
    sid = (req.headers.get("X-Session-Id") or
           SecurityContextHolder.get_session_id() or "").strip()
    return (uid, sid) if uid and sid else None


def _endpoint() -> SandboxEndpoint | None:
    return deps._vnc_binding


@router.get("/vnc")
async def vnc_page(req: Request):
    t = _tenant(req)
    if not t:
        return {"error": "missing X-User-Id or X-Session-Id"}, 400
    ep = _endpoint()
    if not ep:
        return {"error": "sandbox endpoint not initialized"}, 503
    conn = ep.acquire(*t)
    return RedirectResponse(conn.vnc_url)


@router.post("/vnc/release")
async def vnc_release(req: Request):
    t = _tenant(req)
    if not t:
        return {"error": "missing X-User-Id or X-Session-Id"}, 400
    ep = _endpoint()
    if ep:
        ep.release(*t)
    return {"status": "released"}


@router.get("/vnc/status")
async def vnc_status():
    ep = _endpoint()
    return ep.stats() if ep else {"active_bindings": 0}

"""VNC proxy endpoints — route browser traffic to user-specific AIO containers.

All content is proxied through the gateway: the browser never talks
directly to Docker container IPs.  The gateway fetches from the
container and returns to the browser.
"""
from __future__ import annotations

import asyncio
import subprocess
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse, Response

from common.security.context import SecurityContextHolder
from aio_gateway.api import deps

router = APIRouter()
_http = __import__("httpx")


def _container_ip(cid: str) -> str:
    raw = subprocess.run(
        ["docker", "inspect", "-f", "{{.NetworkSettings.IPAddress}}", cid],
        capture_output=True, text=True, timeout=5,
    )
    return raw.stdout.strip()


def _extract_tenant_from_headers(req: Request) -> tuple[str, str]:
    uid = (req.headers.get("X-User-Id") or
           SecurityContextHolder.get_user_id() or "").strip()
    sid = (req.headers.get("X-Session-Id") or
           SecurityContextHolder.get_session_id() or "").strip()
    return uid, sid


def _get_cid(uid: str, sid: str) -> str:
    binding = deps._vnc_binding
    if not binding:
        raise RuntimeError("VNC binding not initialized")
    cid = binding.lookup(uid, sid)
    if not cid:
        cid = binding.acquire(uid, sid)
    return cid


# ---- HTTP proxy for /vnc/ (content proxy, NOT redirect) ----

@router.get("/vnc")
async def vnc_page(req: Request):
    """Proxy VNC index.html from user's container."""
    uid, sid = _extract_tenant_from_headers(req)
    if not uid or not sid:
        return HTMLResponse("<h1>Missing X-User-Id or X-Session-Id</h1>", 400)

    try:
        cid = _get_cid(uid, sid)
    except RuntimeError as e:
        return HTMLResponse(f"<h1>{e}</h1>", 503)

    ip = _container_ip(cid)
    url = f"http://{ip}:8080/vnc/index.html"

    async with _http.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, params={"autoconnect": "true"})
    html = resp.text

    # Rewrite WebSocket path so noVNC connects through gateway
    ws_path = f"/v1/aio/websockify?session_id={sid}"
    html = html.replace(
        "new WebSocket(", f"new WebSocket('{ws_path}',"
    )
    html = html.replace('"websockify"', f'"{ws_path}"')

    return HTMLResponse(html)


@router.get("/vnc/{path:path}")
async def vnc_static(path: str, req: Request):
    """Proxy /vnc/ static assets from user's container."""
    import httpx
    uid, sid = _extract_tenant_from_headers(req)
    cid = _get_cid(uid, sid)
    ip = _container_ip(cid)
    url = f"http://{ip}:8080/vnc/{path}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, params=dict(req.query_params))
    return Response(content=resp.content, status_code=resp.status_code,
                    headers=dict(resp.headers))


# ---- WebSocket proxy for /websockify ----

@router.websocket("/websockify")
async def vnc_websocket(ws: WebSocket, session_id: str = Query(...)):
    """Bidirectional WebSocket relay: browser ↔ container Websockify (6080)."""
    import aiohttp
    from aiohttp import WSMsgType

    # Get user_id from WebSocket headers
    uid = (ws.headers.get("x-user-id") or
           ws.headers.get("X-User-Id") or "").strip()
    sid = session_id.strip()
    if not uid or not sid:
        await ws.close(code=4000, reason="missing user_id or session_id")
        return

    binding = deps._vnc_binding
    if not binding:
        await ws.close(code=4000, reason="VNC binding not initialized")
        return

    cid = binding.lookup(uid, sid)
    if not cid:
        await ws.close(code=4000, reason="no VNC session for this user")
        return

    await ws.accept()
    ip = _container_ip(cid)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(f"ws://{ip}:6080") as container_ws:

                async def browser_to_container():
                    try:
                        async for msg in ws.iter_text():
                            await container_ws.send_str(msg)
                    except WebSocketDisconnect:
                        pass
                    except Exception:
                        pass

                async def container_to_browser():
                    try:
                        async for msg in container_ws:
                            if msg.type == WSMsgType.TEXT:
                                await ws.send_text(msg.data)
                            elif msg.type == WSMsgType.BINARY:
                                await ws.send_bytes(msg.data)
                    except Exception:
                        pass

                binding.heartbeat(uid, sid)
                await asyncio.gather(
                    browser_to_container(),
                    container_to_browser(),
                    return_exceptions=True,
                )
    except Exception:
        pass
    finally:
        # WebSocket 断开不立即释放 — 用户可能刷新页面
        # cleanup_idle 会处理超时
        binding.heartbeat(uid, sid)


# ---- Manage VNC session lifecycle ----

@router.post("/vnc/release")
async def vnc_release(req: Request):
    """Release VNC binding, return container to pool."""
    uid, sid = _extract_tenant_from_headers(req)
    binding = deps._vnc_binding
    if binding:
        binding.release(uid, sid)
    return {"status": "released"}


@router.get("/vnc/status")
async def vnc_status(req: Request):
    """Show all active VNC bindings."""
    binding = deps._vnc_binding
    return binding.stats() if binding else {"active_bindings": 0}

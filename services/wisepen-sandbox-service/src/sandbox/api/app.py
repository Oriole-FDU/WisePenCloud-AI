from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, FastAPI, Query, WebSocket, WebSocketDisconnect
from starlette.types import ASGIApp

from common.mcp import build_tool_meta_router
from sandbox.api.endpoints import health

if TYPE_CHECKING:
    from sandbox.application.browser_service import SandboxBrowserService


def _build_cdp_router(browser_service: SandboxBrowserService) -> APIRouter:
    router = APIRouter()

    @router.websocket("/sandbox/browser/cdp")
    async def browser_cdp_ws(websocket: WebSocket, session_id: str = Query(...)):
        user_id = websocket.headers.get("x-user-id", "")
        async with browser_service.open_cdp_channel(user_id=user_id, session_id=session_id) as upstream:
            await websocket.accept()

            async def client_to_upstream() -> None:
                try:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            return
                        if message.get("text") is not None:
                            await upstream.send(message["text"])
                        elif message.get("bytes") is not None:
                            await upstream.send(message["bytes"])
                except WebSocketDisconnect:
                    return

            async def upstream_to_client() -> None:
                async for message in upstream:
                    if isinstance(message, str):
                        await websocket.send_text(message)
                    else:
                        await websocket.send_bytes(message)

            done, pending = await asyncio.wait(
                {asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)

    return router


def create_app(
    *,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[Any]] | None = None,
    mcp_app: ASGIApp | None = None,
    browser_service: SandboxBrowserService | None = None,
) -> FastAPI:

    app = FastAPI(
        title="WisePen Sandbox Core Service",
        version="0.1.0",
        description="WisePen 内部沙箱核心生命周期服务。",
        openapi_tags=[
            {"name": "health", "description": "进程存活与沙箱池就绪探针。"},
            {"name": "pool", "description": "预热池容量与运行指标接口。"},
        ],
        lifespan=lifespan,
    )

    app.include_router(health.router)
    app.include_router(build_tool_meta_router())
    if mcp_app is not None:
        app.mount("/mcp", mcp_app)
    if browser_service is not None:
        app.include_router(_build_cdp_router(browser_service))
    return app

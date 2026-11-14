from __future__ import annotations

from fastapi import APIRouter

from common.core.domain import R
from common.mcp.registry import get_tool_metas


def build_tool_meta_router() -> APIRouter:
    """返回暴露 `/internal/tools/meta` 的 FastAPI 路由。"""

    router = APIRouter()

    @router.get("/internal/tools/meta")
    async def list_tool_metas() -> R[list]:
        return R.success(get_tool_metas())

    return router


__all__ = ["build_tool_meta_router"]

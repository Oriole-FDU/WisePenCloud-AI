from __future__ import annotations

from fastapi import APIRouter

from common.core.domain import R
from common.mcp.registry import get_tool_categories, get_tool_metas
from common.mcp.tool_meta import ToolCatalogMeta


def build_tool_catalog_router() -> APIRouter:
    """返回包含工具策略与类别说明的内部 catalog 接口"""

    router = APIRouter()

    @router.get("/internal/tools/catalog")
    async def get_tool_catalog() -> R[ToolCatalogMeta]:
        return R.success(ToolCatalogMeta(
            categories=get_tool_categories(),
            tools=get_tool_metas(),
        ))

    return router


__all__ = ["build_tool_catalog_router"]

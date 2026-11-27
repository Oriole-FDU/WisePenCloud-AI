from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from common.mcp.tool_meta import ToolCategoryMeta, ToolMeta

F = TypeVar("F", bound=Callable[..., Any])

_tool_metas: dict[str, ToolMeta] = {}
_tool_categories: dict[str, ToolCategoryMeta] = {}


def register_tool_meta(meta: ToolMeta) -> None:
    """把一条工具元数据注册进当前进程的全局表"""
    if meta.tool_name in _tool_metas:
        raise ValueError(f"duplicate tool meta registered: {meta.tool_name}")
    _tool_metas[meta.tool_name] = meta


def get_tool_metas() -> list[ToolMeta]:
    return list(_tool_metas.values())


def register_tool_category(meta: ToolCategoryMeta) -> None:
    """注册由当前服务拥有的工具类别"""
    if meta.name == "resident":
        raise ValueError("resident is a reserved tool category")
    if meta.name in _tool_categories:
        raise ValueError(f"duplicate tool category registered: {meta.name}")
    _tool_categories[meta.name] = meta


def register_tool_categories(metas: list[ToolCategoryMeta] | tuple[ToolCategoryMeta, ...]) -> None:
    for meta in metas:
        register_tool_category(meta)


def get_tool_categories() -> list[ToolCategoryMeta]:
    return list(_tool_categories.values())


def get_tool_meta_names() -> set[str]:
    return set(_tool_metas)


def assert_tool_catalog_consistent(registered_tool_names: set[str]) -> None:
    """校验工具元数据、真实 MCP 工具和类别定义彼此一致"""
    meta_names = get_tool_meta_names()
    missing_tool = sorted(meta_names - registered_tool_names)
    if missing_tool:
        raise RuntimeError(f"tool meta references unknown tools: {missing_tool}")

    category_names = set(_tool_categories)
    missing_categories = sorted({
        meta.category
        for meta in _tool_metas.values()
        if meta.category != "resident" and meta.category not in category_names
    })
    if missing_categories:
        raise RuntimeError(f"tool meta references unknown categories: {missing_categories}")


def wisepen_mcp_meta(meta: ToolMeta) -> Callable[[F], F]:
    """把 meta 登记到进程内注册表，不改变被装饰函数"""

    def decorator(func: F) -> F:
        register_tool_meta(meta)
        return func

    return decorator


__all__ = [
    "assert_tool_catalog_consistent",
    "get_tool_categories",
    "get_tool_meta_names",
    "get_tool_metas",
    "register_tool_categories",
    "register_tool_category",
    "register_tool_meta",
    "wisepen_mcp_meta",
]

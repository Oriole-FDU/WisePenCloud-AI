from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from common.mcp.tool_meta import ToolMeta

F = TypeVar("F", bound=Callable[..., Any])

_tool_metas: dict[str, ToolMeta] = {}


def register_tool_meta(meta: ToolMeta) -> None:
    """把一条工具元数据注册进当前进程的全局表"""
    if meta.tool_name in _tool_metas:
        raise ValueError(f"duplicate tool meta registered: {meta.tool_name}")
    _tool_metas[meta.tool_name] = meta


def get_tool_metas() -> list[ToolMeta]:
    return list(_tool_metas.values())


def get_tool_meta_names() -> set[str]:
    return set(_tool_metas)


def assert_tool_meta_consistent(registered_tool_names: set[str]) -> None:
    """校验每个已声明的 meta 都能匹配到真实注册的工具

    工具可以暂时没有 meta（即尚未暴露给 Chat 的新工具）；但一旦声明了 meta，
    其 tool_name 必须命中一个真实注册的 FastMCP 工具，否则说明 name 漂移。
    """
    meta_names = get_tool_meta_names()
    missing_tool = sorted(meta_names - registered_tool_names)
    if missing_tool:
        raise RuntimeError(
            f"tool meta references unknown tools: {missing_tool}"
        )


def wisepen_mcp_meta(meta: ToolMeta) -> Callable[[F], F]:
    """把 meta 登记到进程内注册表，不改变被装饰函数"""

    def decorator(func: F) -> F:
        register_tool_meta(meta)
        return func

    return decorator


__all__ = [
    "assert_tool_meta_consistent",
    "get_tool_meta_names",
    "get_tool_metas",
    "register_tool_meta",
    "wisepen_mcp_meta",
]

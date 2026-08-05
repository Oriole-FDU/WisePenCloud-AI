from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

from mcp.server.fastmcp import Context

MCP_TOOL_CONFIG_META_KEY = "wisepen/tool_config"
MCP_TOOL_CONTEXT_META_KEY = "wisepen/tool_context"


def get_tool_config_value(ctx: Context, key: str) -> object | None:
    meta = ctx.request_context.meta
    if not meta:
        return None
    tool_config = (meta.model_extra or {}).get(MCP_TOOL_CONFIG_META_KEY)
    return tool_config.get(key) if isinstance(tool_config, Mapping) else None


def get_tool_context_value(ctx: Context, key: str) -> object | None:
    meta = ctx.request_context.meta
    if not meta:
        return None
    tool_context = (meta.model_extra or {}).get(MCP_TOOL_CONTEXT_META_KEY)
    return tool_context.get(key) if isinstance(tool_context, Mapping) else None


class CacheableText(TypedDict):
    text: str
    is_md: bool
    metadata: dict[str, object]


class ToolReturn(TypedDict):
    visible_result: dict[str, Any]
    cacheable_texts: list[CacheableText]

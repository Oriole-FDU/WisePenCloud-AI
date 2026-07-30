from __future__ import annotations

from typing import Any, TypedDict

MCP_TOOL_CONFIG_META_KEY = "wisepen/tool_config"
MCP_TOOL_CONTEXT_META_KEY = "wisepen/tool_context"


class CacheableText(TypedDict):
    text: str
    is_md: bool
    metadata: dict[str, object]


class ToolReturn(TypedDict):
    visible_result: dict[str, Any]
    cacheable_texts: list[CacheableText]

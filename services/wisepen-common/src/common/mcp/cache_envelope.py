from __future__ import annotations

import inspect
import json
from collections.abc import Callable
from functools import wraps
from typing import Any

from pydantic import TypeAdapter

MCP_CACHE_PATHS_KEY = "__mcp_cache_paths__"
MCP_CACHE_PAYLOAD_KEY = "payload"

_JSON_ADAPTER = TypeAdapter(Any)


def pack_cache_envelope(payload: Any, *, paths: tuple[str, ...]) -> dict[str, Any]:
    """把业务载荷与可缓存路径打包为 MCP 缓存信封
    信封是 MCP 侧对业务载荷 + 可缓存路径的显式标记
    {"__mcp_cache_paths__": ["text"], "payload": {...}}
    """
    return {
        MCP_CACHE_PATHS_KEY: list(paths),
        MCP_CACHE_PAYLOAD_KEY: _JSON_ADAPTER.dump_python(payload, mode="json"),
    }


def unpack_cache_envelope(content: str) -> tuple[tuple[str, ...], Any] | None:
    """从工具输出 JSON 字符串中识别并解包缓存信封"""

    try:
        envelope = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return None

    if (
        not isinstance(envelope, dict)
        or set(envelope) != {MCP_CACHE_PATHS_KEY, MCP_CACHE_PAYLOAD_KEY}
    ):
        return None

    paths = envelope[MCP_CACHE_PATHS_KEY]
    if not isinstance(paths, list) or not all(
        isinstance(path, str) and bool(path.strip()) for path in paths
    ):
        return None

    return tuple(paths), envelope[MCP_CACHE_PAYLOAD_KEY]


def cacheable_tool_output(
    func: Callable[..., Any] | None = None,
    *,
    paths: tuple[str, ...] = (),
) -> Callable[..., Any]:
    """把 MCP 工具结果包装为缓存信封"""
    if not isinstance(paths, tuple) or any(
        not isinstance(path, str) or not path.strip() for path in paths
    ):
        raise TypeError(
            "cacheable_tool_output paths must be a tuple of non-empty strings"
        )

    def decorate(target: Callable[..., Any]) -> Callable[..., Any]:
        if not inspect.iscoroutinefunction(target):
            raise TypeError("cacheable_tool_output requires an async function")

        @wraps(target)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raw_result = await target(*args, **kwargs)
            return pack_cache_envelope(raw_result, paths=paths)

        signature = inspect.signature(target)
        wrapper.__signature__ = signature.replace(return_annotation=dict[str, Any])
        wrapper.__annotations__ = {
            **(wrapper.__annotations__ or {}),
            "return": dict[str, Any],
        }
        return wrapper

    if func is None:
        return decorate
    return decorate(func)

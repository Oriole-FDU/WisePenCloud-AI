from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any

from common.mcp import pack_cache_envelope


def cacheable_tool_output(func: Callable[..., Any] | None = None, *, paths: tuple[str, ...] = ()) -> Callable[..., Any]:
    """把 MCP 工具结果包装为缓存信封"""

    if not isinstance(paths, tuple) or any(not isinstance(path, str) or not path.strip() for path in paths):
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
        return wrapper

    if func is None:
        return decorate
    return decorate(func)

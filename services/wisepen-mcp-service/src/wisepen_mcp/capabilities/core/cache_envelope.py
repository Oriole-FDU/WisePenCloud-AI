from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any

from pydantic import TypeAdapter

MCP_CACHE_PATHS_KEY = "__mcp_cache_paths__"
MCP_CACHE_PAYLOAD_KEY = "payload"
_JSON_ADAPTER = TypeAdapter(Any)


def cacheable_tool_output(
    func: Callable[..., Any] | None = None,
    *,
    paths: tuple[str, ...] = (),
) -> Callable[..., Any]:
    """把 MCP 工具结果包装为 Host 可识别的缓存信封。

    远程服务只声明业务字段路径并完成 JSON 降维；缓存、Session 和正文
    存储仍由 Chat Host 负责。返回注解被改为通用对象，以匹配信封的真实
    structured output，而原函数的参数签名保持不变供 FastMCP 生成 schema。
    """

    if not isinstance(paths, tuple) or any(
        not isinstance(path, str) or not path.strip() for path in paths
    ):
        raise TypeError("cacheable_tool_output paths must be a tuple of non-empty strings")

    def decorate(target: Callable[..., Any]) -> Callable[..., Any]:
        if not inspect.iscoroutinefunction(target):
            raise TypeError("cacheable_tool_output requires an async function")

        @wraps(target)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raw_result = await target(*args, **kwargs)
            payload = _JSON_ADAPTER.dump_python(raw_result, mode="json")
            return {
                MCP_CACHE_PATHS_KEY: list(paths),
                MCP_CACHE_PAYLOAD_KEY: payload,
            }

        signature = inspect.signature(target)
        wrapper.__signature__ = signature.replace(return_annotation=dict[str, Any])
        return wrapper

    if func is None:
        return decorate
    return decorate(func)

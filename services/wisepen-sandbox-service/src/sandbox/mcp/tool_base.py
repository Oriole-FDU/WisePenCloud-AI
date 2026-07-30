"""SandboxScriptTool — 跨层工具抽象接口。

Gateway MCP 工具和 Chat 侧策略配置共享同一 SandboxToolSpec 定义。
新增沙箱工具只需定义 name + schema + handler 三要素。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Literal

from common.logger import error as log_error
from sandbox.mcp.context import extract_tenant, build_translator
from sandbox.gateway.isolation import PathValidationError

ExecutionMode = Literal["container", "host_cache"]


@dataclass(frozen=True)
class SandboxToolSpec:
    """沙箱工具的纯数据描述 — Gateway MCP 和 Chat Policy 的单一定义来源。

    不可变，所有字段在定义时确定。
    """
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema {type, properties, required}
    mode: ExecutionMode = "container"

    @classmethod
    def from_json_schema(
        cls,
        name: str,
        description: str,
        properties: dict[str, dict[str, Any]],
        required: list[str] | None = None,
        mode: ExecutionMode = "container",
    ) -> SandboxToolSpec:
        return cls(
            name=name,
            description=description,
            input_schema={
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
            mode=mode,
        )


@dataclass
class ToolContext:
    """注入到 SandboxHandler 的运行时上下文。

    container 模式: session_pool 可用，可通过 acquire 获取容器
    host_cache 模式: session_pool 为 None，handler 直接从 host filesystem 读取
    """
    uid: str
    sid: str
    translator: Any  # PathTranslator
    session_pool: Any = None
    executor: Any = None

    def translate(self, path: str) -> str:
        return self.translator.translate(path)

    def reverse(self, physical_path: str) -> str:
        return self.translator.reverse(physical_path)


SandboxHandler = Callable[..., Coroutine[Any, Any, Any]]


def _scrub_result(result: Any, translator) -> Any:
    """递归替换响应中的物理路径为虚拟路径。"""
    if isinstance(result, dict):
        scrubbed = {}
        for k, v in result.items():
            if k in ("file", "path") and isinstance(v, str):
                scrubbed[k] = translator.reverse(v)
            elif isinstance(v, str) and translator.physical_root in v:
                scrubbed[k] = v.replace(translator.physical_root, "/workspace")
            elif isinstance(v, list):
                scrubbed[k] = [_scrub_result(item, translator) for item in v]
            elif isinstance(v, dict):
                scrubbed[k] = _scrub_result(v, translator)
            else:
                scrubbed[k] = v
        return scrubbed
    return result


def _json_param_default(json_type: str) -> str:
    """Get Python default value string for a JSON Schema type."""
    if json_type == "integer":
        return "0"
    elif json_type == "number":
        return "0.0"
    elif json_type == "boolean":
        return "False"
    return "None"


def register_sandbox_tool(
    mcp: Any,  # FastMCP
    spec: SandboxToolSpec,
    handler: SandboxHandler,
    session_pool: Any,
    executor: Any = None,
) -> None:
    """将一个 SandboxToolSpec + handler 注册为 FastMCP MCP 工具。

    通过 exec() 生成具名参数的 wrapper function，供 @mcp.tool() 装饰。
    内部处理 tenant 提取、路径翻译、错误处理、响应清理等样板逻辑。
    """
    wrapper = _build_wrapper(spec, handler, session_pool, executor)

    # Apply @mcp.tool decorator with name and description from spec
    decorated = mcp.tool(name=spec.name, description=spec.description)(wrapper)

    # The decorator returns the original function; FastMCP stores it internally
    # We don't need the return value — FastMCP registered it via the decorator


def _build_wrapper(
    spec: SandboxToolSpec,
    handler: SandboxHandler,
    session_pool: Any,
    executor: Any,
):
    """生成带有正确签名的 async wrapper function。

    FastMCP 从函数签名派生 JSON Schema，因此 wrapper 的参数签名必须
    与 SandboxToolSpec.input_schema 完全匹配（参数名、类型注解、默认值）。
    样板逻辑（tenant 提取、路径翻译、错误处理、scrub）统一在这里处理。
    """
    properties: dict = spec.input_schema.get("properties", {})
    required: list = spec.input_schema.get("required", [])

    param_parts: list[str] = []
    call_parts: list[str] = []
    type_map = {
        "string": (str, None),
        "integer": (int, 0),
        "number": (float, 0.0),
        "boolean": (bool, False),
    }

    for name, prop in properties.items():
        prop_type = prop.get("type", "string")
        py_type_info = type_map.get(prop_type, (str, None))
        py_type, default_val = py_type_info

        if name in required:
            param_parts.append(f"{name}: {py_type.__name__}")
            call_parts.append(f"{name}={name}")
        else:
            # Use the declared default from the schema, or type default
            decl_default = prop.get("default")
            if decl_default is None:
                decl_default = default_val
            if isinstance(decl_default, str):
                default_repr = repr(decl_default)
            elif isinstance(decl_default, bool):
                default_repr = str(decl_default)
            else:
                default_repr = str(decl_default)
            param_parts.append(f"{name}: {py_type.__name__} = {default_repr}")
            call_parts.append(f"{name}={name}")

    param_str = ", ".join(param_parts)
    call_str = ", ".join(call_parts)

    # Build mode-specific setup
    if spec.mode == "host_cache":
        # host_cache: no session_pool, executor available for dev mode mock
        pool_setup = "ctx = ToolContext(uid=uid, sid=sid, translator=translator, executor=_exec)"
    else:
        pool_setup = "ctx = ToolContext(uid=uid, sid=sid, translator=translator, session_pool=_pool, executor=_exec)"

    code = f"""
async def _wrapper({param_str}) -> str:
    from sandbox.mcp.tool_base import ToolContext, _scrub_result as _scrub

    uid, sid = _extract_tenant()
    if not uid or not sid:
        return _json.dumps({{"error": "missing X-User-Id or X-Session-Id"}})
    try:
        translator = _build_translator(uid, sid)
    except _PathValidationError as e:
        return _json.dumps({{"error": str(e)}})

    {pool_setup}

    try:
        result = await _handler(ctx, {call_str})
        if isinstance(result, dict):
            result = _scrub(result, translator)
        return _json.dumps(result)
    except _PathValidationError as e:
        return _json.dumps({{"error": str(e)}})
    except Exception as e:
        _log_error("mcp {spec.name} failed", exc=e)
        return _json.dumps({{"error": "{spec.name} failed: {{e}}"}})
"""
    ns: dict[str, Any] = {
        "_wrapper": None,
        "_handler": handler,
        "_pool": session_pool,
        "_exec": executor,
        "_extract_tenant": extract_tenant,
        "_build_translator": build_translator,
        "_PathValidationError": PathValidationError,
        "_log_error": log_error,
        "_json": json,
    }
    exec(compile(code, f"<sandbox_tool:{spec.name}>", "exec"), ns)
    return ns["_wrapper"]

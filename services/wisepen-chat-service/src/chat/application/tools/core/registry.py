from collections.abc import Iterable
from typing import Any

from chat.application.tools.core.definition import Tool
from chat.application.tools.core.llm.renderer import schema_renderer

class ToolScope:
    """一次请求内的工具可见性和可信上下文快照"""

    def __init__(self, *, tools: dict[str, Tool], context: dict[str, Any] | None) -> None:
        self._tools = dict(tools)
        self._context = dict(context or {})
        self._schemas: list[dict[str, Any]] = [schema_renderer(tool.definition.llm_spec) for tool in self._tools.values()]

    def schemas(self) -> list[dict[str, Any]]:
        return list(self._schemas)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    @property
    def context(self) -> dict[str, Any]:
        return dict(self._context)

    def __len__(self) -> int:
        return len(self._tools)

class ToolRegistry:
    """全局工具注册表，负责派生请求级工具视图"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.definition.llm_spec.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        """返回全局已注册工具的 schema。

        该方法仅用于诊断和测试。运行期 LLM 调用必须使用 ToolScope.schemas()，
        确保已应用当前请求的 expose/allow/deny 过滤。
        """
        return [schema_renderer(tool.definition.llm_spec) for tool in self._tools.values()]

    def derive(
        self,
        *,
        tool_context: dict[str, Any] | None = None,
        expose_tool_name_set: set[str] | None = None,
        allow_tool_name_set: set[str] | None = None,
        deny_tool_name_set: set[str] | None = None,
    ) -> ToolScope:
        expose_tool_name_set = expose_tool_name_set or set()
        deny_tool_name_set = deny_tool_name_set or set()

        tools: dict[str, Tool] = dict(self._tools)

        filtered_tools: dict[str, Tool] = {}
        for name, tool in tools.items():
            policy = tool.definition.policy

            if not policy.expose_by_default:
                if name in expose_tool_name_set:
                    filtered_tools[name] = tool
                continue
            if allow_tool_name_set is not None and name not in allow_tool_name_set:
                continue
            if policy.expose_by_default and name in deny_tool_name_set:
                continue

            filtered_tools[name] = tool

        context = dict(tool_context or {})

        return ToolScope(tools=filtered_tools, context=context)

    def __len__(self) -> int:
        return len(self._tools)

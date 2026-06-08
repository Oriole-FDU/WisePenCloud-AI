from typing import Any, Dict, Iterable, List, Optional, Sequence

from chat.domain.interfaces.tool import BaseTool


class ToolScope:
    """
    一次请求内的"可见工具集合 + 延迟工具候选池 + 安全上下文"。

    初始 schema 尽量小；tool_search 命中后可把 deferred 工具加入当前可见集，
    QueryLoopRuntime 下一轮会重新读取 schemas()，从而暴露新增工具。
    """

    def __init__(
        self,
        *,
        tools: Dict[str, BaseTool],
        deferred_tools: Optional[Dict[str, BaseTool]] = None,
        context: Dict[str, Any],
    ) -> None:
        self._tools = dict(tools)
        self._deferred_tools = dict(deferred_tools or {})
        self._context = context
        self._schemas: List[Dict[str, Any]] = []
        self._rebuild_schemas()

    def _rebuild_schemas(self) -> None:
        self._schemas = [t.get_tool_schema() for t in self._tools.values()]

    def schemas(self) -> List[Dict[str, Any]]:
        return list(self._schemas)

    def get(self, name: str) -> Optional[BaseTool]:
        """
        按名查找工具
        未在 scope 视图内返回 None
        """
        return self._tools.get(name)

    def list_deferred_tools(self) -> List[BaseTool]:
        """返回尚未暴露的延迟工具，供 tool_search 检索。"""
        return [
            tool
            for name, tool in self._deferred_tools.items()
            if name not in self._tools
        ]

    def expose_deferred_tools(self, names: Iterable[str]) -> List[str]:
        """
        将命中的延迟工具加入当前可见集合。
        返回实际新增的工具名；已可见工具视为 no-op。
        """
        exposed: List[str] = []
        for name in names:
            if name in self._tools:
                continue
            tool = self._deferred_tools.get(name)
            if tool is None:
                continue
            self._tools[name] = tool
            exposed.append(name)
        if exposed:
            self._rebuild_schemas()
        return exposed

    def visible_tool_names(self) -> Sequence[str]:
        return tuple(self._tools.keys())

    @property
    def context(self) -> Dict[str, Any]:
        context = dict(self._context)
        context["tool_scope"] = self
        return context

    def is_ephemeral(self, name: str) -> bool:
        t = self.get(name)
        return bool(t and t.is_ephemeral_output)  # 未在 Scope 视图内的视为 False

    def __len__(self) -> int:
        return len(self._tools)

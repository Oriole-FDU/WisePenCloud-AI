from typing import Dict, List
from chat.domain.interfaces.tool import BaseTool


class ToolRegistry:
    """
    工具注册表：维护 name → BaseTool 的映射，供 QueryLoopRuntime 动态查询和调度。
    实例由 DI 容器统一管理，不同 Agent 角色可注入不同工具集。
    """

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册一个工具，以 tool.name 为键。重复注册会覆盖旧实例。"""
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        """
        按名称查找工具。
        :raises KeyError: 工具未注册时抛出，调用方应将其转化为降级消息而非向上传播。
        """
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' is not registered in ToolRegistry.")
        return self._tools[name]

    def schemas(self) -> List[dict]:
        """导出所有已注册工具的 OpenAI Function Calling 格式 schema，直接传给 tools= 参数。"""
        return [tool.get_tool_schema() for tool in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)


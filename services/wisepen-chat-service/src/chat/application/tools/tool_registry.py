from typing import Any, Dict, Iterable, List, Optional, Set

from common.logger import log_event

from chat.domain.interfaces.tool import BaseTool
from chat.application.tools.tool_scope import ToolScope


class ToolRegistry:
    """
    工具注册表：维护 name → BaseTool 的全局映射，供请求期派生 ToolScope
    实例由 DI 容器统一管理，不同 Agent 角色可注入不同工具集

    register 仅在启动装配阶段调用，之后只读
    请求期的"本轮临时扩展 + 屏蔽"通过 derive() 返回的 ToolScope 表达
    """

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册一个工具，以 tool.name 为键。重复注册会覆盖旧实例"""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[BaseTool]:
        """按名称查找工具，未注册时返回 None"""
        return self._tools.get(name)

    def schemas(self) -> List[Dict[str, Any]]:
        """导出全量工具 schema"""
        return [tool.get_tool_schema() for tool in self._tools.values()]

    def derive(
        self,
        *,
        session_id: str,
        tool_context: Optional[Dict[str, Any]] = None,
        runtime_discovered_tools: Optional[Iterable[BaseTool]] = None,
        expose_tool_name_set: Optional[Set[str]] = None,
        allow_tool_name_set: Optional[Set[str]] = None,
        deny_tool_name_set: Optional[Set[str]] = None,
    ) -> ToolScope:
        """
        派生一个请求级 ToolScope 快照
        :param runtime_discovered_tools:   
                         运行时动态发现的工具（非 boot 时已知）。
        :param expose:   系统解禁集合，reserved 工具只有在此集合中才可见
                         例：skill 命中时 Coordinator 传 {"load_skill", "load_skill_asset"}
        :param tool_context: 工具上下文
        :param allow:    用户白名单；None = 不限制
        :param deny:     用户黑名单；对 reserved+被 expose 的工具无效
        """
        expose_tool_name_set = expose_tool_name_set or set()

        tools: Dict[str, BaseTool] = dict(self._tools)
        for t in runtime_discovered_tools or []:
            tools[t.name] = t

        filtered_tools: Dict[str, BaseTool] = {}
        for name, tool in tools.items():
            # reserved 默认隐藏，仅当系统显式 expose 才可见
            if tool.reserved:
                if name in expose_tool_name_set:
                    filtered_tools[name] = tool
                continue
            else:
                # 未指定黑白名单，则默认保留
                if allow_tool_name_set is None and deny_tool_name_set is None:
                    filtered_tools[name] = tool
                    continue
                # 白名单优先
                if allow_tool_name_set is not None and name in allow_tool_name_set:
                    filtered_tools[name] = tool
                    continue
                # 黑名单
                if deny_tool_name_set is not None and name not in deny_tool_name_set:
                    filtered_tools[name] = tool
                    continue

        return ToolScope(tools=filtered_tools, context=tool_context)

    def __len__(self) -> int:
        return len(self._tools)

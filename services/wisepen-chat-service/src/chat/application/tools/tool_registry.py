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
        expose: Optional[Set[str]] = None,
        allow: Optional[Set[str]] = None,
        deny: Optional[Set[str]] = None,
    ) -> ToolScope:
        """
        派生一个请求级 ToolScope 快照

        过滤管道（顺序严格）：
          1) base = 全局 registry 的所有工具
          3) reserved=True 的工具：只有 name 在 expose 中才保留；否则默认隐藏
          4) allow：None = 不限制；非 None = 白名单模式，只保留集合内的
          5) deny：用户级屏蔽
             - 被 expose 的 reserved 工具豁免 deny（用户权力止步于系统 expose 的工具）
             - 非 reserved 工具按 deny 剔除
          6) context = {**overlay, "session_id": ..., "user_id": ...}——保留字段强制覆盖

        :param runtime_discovered_tools:   
                         运行时动态发现的工具（非 boot 时已知）。
        :param expose:   系统解禁集合，reserved 工具只有在此集合中才可见
                         例：skill 命中时 Coordinator 传 {"load_skill", "load_skill_asset"}
        :param tool_context: 工具上下文
        :param allow:    用户白名单；None = 不限制
        :param deny:     用户黑名单；对 reserved+被 expose 的工具无效
        """
        expose_set = expose or set()
        deny_set = deny or set()

        tools: Dict[str, BaseTool] = dict(self._tools)
        for t in runtime_discovered_tools or []:
            tools[t.name] = t

        final: Dict[str, BaseTool] = {}
        for name, tool in tools.items():
            # reserved 默认隐藏，仅当系统显式 expose 才可见
            if tool.reserved and name not in expose_set:
                continue
            # 白名单
            if allow is not None and name not in allow:
                continue
            # 黑名单：reserved+被 expose 的工具豁免；其它按 deny 剔除
            if name in deny_set:
                if tool.reserved and name in expose_set:
                    log_event(
                        "Tool deny 被 reserved expose 豁免",
                        name=name,
                        session_id=session_id,
                    )
                else:
                    continue
            final[name] = tool

        # reserved-key 强制覆盖：先放业务 overlay，再用 session_id/user_id 盖回
        context: Dict[str, Any] = {
            **(overlay_context or {}),
            "session_id": session_id,
            "user_id": user_id,
        }

        return ToolScope(tools=final, context=context)

    def __len__(self) -> int:
        return len(self._tools)

from typing import Dict, List, Any, Optional

from chat.domain.interfaces.tool import BaseTool


class ToolScope:
    """
    一次请求内可见的"工具集合 + 安全上下文"的不可变快照
    """

    def __init__(
        self,
        *,
        tools: Dict[str, BaseTool],
        context: Dict[str, Any],
    ) -> None:
        self._tools = tools
        self._context = context
        # 构造期固化：之后 stream_chat_with_tool_calling 每个 step 零开销读
        self._schemas: List[Dict[str, Any]] = [
            t.get_tool_schema() for t in tools.values()
        ]

    def schemas(self) -> List[Dict[str, Any]]:
        return list(self._schemas)

    def get(self, name: str) -> Optional[BaseTool]:
        """
        按名查找工具
        未在 scope 视图内返回 None
        """
        return self._tools.get(name)

    @property
    def context(self) -> Dict[str, Any]:
        return dict(self._context)

    def is_ephemeral(self, name: str) -> bool:
        t = self.get(name)
        return bool(t and t.is_ephemeral_output) # 未在 Scope 视图内的视为 False

    def __len__(self) -> int:
        return len(self._tools)

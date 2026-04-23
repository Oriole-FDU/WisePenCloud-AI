# src/chat/domain/interfaces/tool.py
from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: pass

    @property
    @abstractmethod
    def description(self) -> str: pass

    @property
    @abstractmethod
    def parameters_schema(self) -> Dict[str, Any]: pass

    @property
    def is_ephemeral_output(self) -> bool:
        """
        True 表示本工具的输出属于"仅本轮工作内可见"的脚手架（如 Skill 正文加载）
        QueryLoopRuntime 会把对应 TOOL 消息标 ephemeral=True
        ChatTurnFinalizer 在持久化前会将其 content 置换为占位符以防上下文膨胀
        False 表示本工具的输出属于对话事实，应进入 durable 历史
        """
        return False

    @property
    def reserved(self) -> bool:
        """
        True 表示本工具是"系统受控可见性"的：
        默认隐藏，必须由系统在派生 scope 时通过 expose 集合显式解禁才会进入 LLM 视图
        一旦被 expose，就不再受用户级 deny 影响

        False(默认值)表示本工具是普通业务工具.可由 allow/deny 筛选
        """
        return False

    def get_tool_schema(self) -> Dict[str, Any]:
        """生成 LiteLLM/OpenAI 兼容的 tools 结构"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema
            }
        }

    @abstractmethod
    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        """
        执行工具逻辑。
        :param context: 系统强注入的安全上下文（session_id、user_id 等），
                        绝不由 LLM 生成，由 QueryLoopRuntime 在调度时直接写入，防止越权。
        :param kwargs:  LLM 从对话中提取的纯业务参数（keyword、时间范围等）。
        """
        pass

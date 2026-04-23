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

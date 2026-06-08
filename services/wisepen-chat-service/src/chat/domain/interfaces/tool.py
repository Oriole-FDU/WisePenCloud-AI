from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, Dict

_TOOL_NAMESPACE_DESCRIPTIONS = {
    "browser": "Interactive browser control and page observation tools.",
    "chart": "Chart generation, plotting, and note/table visualization tools.",
    "document": "Document parsing, conversion, and export tools.",
    "evidence_access": "Cached tool-content reading and evidence reranking infrastructure.",
    "language": "Language assistance tools such as translation.",
    "math_solver": "Math, symbolic calculation, and runtime solver tools.",
    "retrieval": "Conversation history and knowledge-base retrieval tools.",
    "skill": "Skill loading and skill bundle management tools.",
    "text": "Text analysis and counting tools.",
    "web": "Web search, fetch, and crawl tools.",
}

class ToolExposure(StrEnum):
    """
    控制普通工具何时把完整 schema 暴露给 LLM。

    BOOTSTRAP: 首轮直接可见，用于工具发现、内容续读等 infra 能力。
    DEFERRED: 默认延迟暴露，必须先通过 tool_search 选中。
    """

    BOOTSTRAP = "bootstrap"
    DEFERRED = "deferred"


class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        pass

    @property
    @abstractmethod
    def parameters_schema(self) -> Dict[str, Any]:
        pass

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

    @property
    def exposure(self) -> ToolExposure:
        """
        普通工具默认延迟暴露，避免首轮 schema 膨胀，并防止基础工具抢跑 Skill。
        reserved 工具不看本属性，仍由系统 expose 集合强控。
        """
        return ToolExposure.DEFERRED

    @property
    def search_hint(self) -> str:
        """
        给 tool_search 的短检索提示。默认复用 description；具体工具可覆盖成更短、
        更稳定的能力短语。
        """
        return self.description

    @property
    def namespaces(self) -> tuple[str, ...]:
        """
        工具族命名空间，必须由工具显式声明。
        一个工具可以属于多个 namespace，用于处理交叉能力边界。
        """
        return ()

    def namespace_description(self, namespace: str) -> str:
        return _TOOL_NAMESPACE_DESCRIPTIONS.get(namespace, f"{namespace} tool family.")

    def get_tool_schema(self) -> Dict[str, Any]:
        """生成 LiteLLM/OpenAI 兼容的 tools 结构"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
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

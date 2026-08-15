from enum import Enum
import jieba
from typing import Dict, Any, Optional, List, TYPE_CHECKING
from datetime import datetime, timezone
from copy import deepcopy
from beanie import Document
from pydantic import Field, BaseModel, ConfigDict
from pymongo import IndexModel, ASCENDING

from chat.domain.entities.model import ModelFamily, ModelScope
from chat.domain.entities.provider import ProviderScope, ProviderType

if TYPE_CHECKING:
    from chat.domain.repositories.model_repo import ModelRequestInfo


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCallMessage(BaseModel):
    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class VisionImage(BaseModel):
    """仅用于单次模型调用的内联图片，不参与消息持久化。"""
    media_type: str
    base64_data: str


class MessageAttachmentRef(BaseModel):
    attachment_id: str
    kind: str
    name: str
    extension: Optional[str] = None
    file_size: Optional[int] = None
    mime_type: Optional[str] = None
    is_image: bool = False


class MessageModelInfo(BaseModel):
    """消息持久化用的模型安全快照"""
    model_config = ConfigDict(populate_by_name=True)

    model_id: str
    provider_id: str
    provider_scope: Optional[ProviderScope] = None
    provider_type: ProviderType
    model_family: ModelFamily
    model_name: str
    model_scope: ModelScope
    support_tools: bool
    context_window_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    runtime_options: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_model_request(cls, model_request: "ModelRequestInfo") -> "MessageModelInfo":
        return cls(
            model_id=str(model_request.model_id),
            provider_id=str(model_request.provider_id),
            provider_scope=model_request.provider_scope,
            provider_type=model_request.provider_type,
            model_family=model_request.model.model_family,
            model_name=model_request.model_name,
            model_scope=model_request.model_scope,
            support_tools=model_request.support_tools,
            context_window_tokens=model_request.context_window_tokens,
            max_output_tokens=model_request.max_output_tokens,
            runtime_options=model_request.runtime_options or {},
        )


class ChatMessage(Document):
    """单条消息实体（Beanie Document，映射到 chat_messages 集合）"""
    session_id: str
    role: Role # 消息标识

    # 生成该消息所用的模型安全快照，仅 assistant 消息必填
    model_info: Optional[MessageModelInfo] = None
    # 原生载荷，仅 assistant 消息必填
    provider_payload: Optional[Dict[str, Any]] = None  # LLMProvider的原生载荷，用于历史回放

    # 仅 tool 消息必填
    # 生成该消息所用的工具 tool_name 和 请求的 tool_call_id
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    # 工具消息的持久化占位器
    persisted_output_placeholder: str | None = Field(default=None, exclude=True)

    # 仅 assistant 消息必填
    reasoning_content: Optional[str] = None  # 大模型的推理/思考内容
    tool_calls: Optional[List[ToolCallMessage]] = None
    token_usage: int = 0

    content: Optional[str] = None   # 返回内容
    content_token_count: int = 0 # 消息 token 计数，用于上下文压缩

    # 仅运行时使用，由 Provider 适配器投影为各自的多模态输入协议
    imgs: List[VisionImage] = Field(default_factory=list, exclude=True)

    # 持久化的附件快照；图片二进制仅保留在运行时 imgs 中
    attachments: List[MessageAttachmentRef] = Field(default_factory=list)

    # 内容搜索分词，用于规避 MongoDB 中文分词缺陷
    content_search_tokens: Optional[str] = None

    # 元信息
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "wisepen_chat_message"  # MongoDB 集合名
        indexes = [
            # 按会话拉取历史记录的核心查询路径，防全表扫描
            IndexModel([("session_id", ASCENDING), ("created_at", ASCENDING)]),
            IndexModel([("content_search_tokens", "text")]),
        ]

    def build_search_tokens(self) -> None:
        """
        在保存前调用此方法。
        使用搜索引擎模式的分词（cut_for_search），最大化召回率。
        例如："软件工程架构" -> "软件 工程 软件工程 架构"
        """
        if self.content:
            # 过滤掉单字和标点符号，用空格拼接
            words = jieba.cut_for_search(self.content)
            self.content_search_tokens = " ".join([w for w in words if len(w.strip()) > 1])

    @classmethod
    def for_persistence(cls, messages: List["ChatMessage"]) -> List["ChatMessage"]:
        """
        若工具结果不应完整持久化，则用占位符替换 content，避免把大段原始工具输出写入长期存储
        """
        persisted_messages = deepcopy(messages)
        for message in persisted_messages:
            if message.persisted_output_placeholder is None:
                continue
            message.content = message.persisted_output_placeholder
            message.persisted_output_placeholder = None
        return persisted_messages

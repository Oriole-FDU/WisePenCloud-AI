from enum import Enum
import jieba
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from beanie import Document
from pydantic import Field, ConfigDict
from pymongo import IndexModel, ASCENDING


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ChatMessage(Document):
    """单条消息实体（Beanie Document，映射到 chat_messages 集合）"""
    session_id: str
    role: Role
    model_id: Optional[int] = None  # 生成该消息所用的模型 ID，仅 assistant 消息必填
    content: Optional[str] = None   # 大模型在返回 tool_calls 时 content 经常为 None
    reasoning_content: Optional[str] = None  # 大模型的推理/思考内容（DeepSeek R1 等）
    search_tokens: Optional[str] = None # 专门用于规避 MongoDB 中文分词缺陷的隐藏字段

    token_count: Optional[int] = None # 消息内容对应的 Token 数，随消息创建时一次性计算并持久化
    metadata: Dict[str, Any] = Field(default_factory=dict)

    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

    # 仅本轮工作内可见标识
    # True 时可对 ASSISTANT 消息整条丢弃、对 TOOL 消息 content 置换为占位符
    # 确保 Skill 等脚手架内容不污染 durable 历史
    ephemeral: bool = False

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(frozen=False)

    class Settings:
        name = "wisepen_chat_message"  # MongoDB 集合名
        indexes = [
            # 按会话拉取历史记录的核心查询路径，防全表扫描
            IndexModel([("session_id", ASCENDING), ("created_at", ASCENDING)]),
            # 支持 Tool Calling 的全文关键词检索
            IndexModel([("search_tokens", "text")]),
        ]

    @property
    def is_human(self) -> bool:
        return self.role == Role.USER


    def build_search_tokens(self) -> None:
        """
        在保存前调用此方法。
        使用搜索引擎模式的分词（cut_for_search），最大化召回率。
        例如："软件工程架构" -> "软件 工程 软件工程 架构"
        """
        if self.content:
            # 过滤掉单字和标点符号，用空格拼接
            words = jieba.cut_for_search(self.content)
            self.search_tokens = " ".join([w for w in words if len(w.strip()) > 1])

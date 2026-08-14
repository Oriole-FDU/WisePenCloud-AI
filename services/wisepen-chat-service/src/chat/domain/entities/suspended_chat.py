from __future__ import annotations

import base64
import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, List, Optional

from beanie import Document
from pydantic import Field, field_validator, field_serializer
from pymongo import ASCENDING, IndexModel

if TYPE_CHECKING:
    from chat.application.agents import AgentSpec
    from chat.application.chat_context_assembler import WindowedMessages
    from chat.application.events import TurnSuspension
    from chat.domain.entities import ChatMessage
    from chat.domain.repositories.model_repo import ModelRequestInfo


@dataclass
class SuspendedTurnContext:
    model_info: ModelRequestInfo
    agent_spec: AgentSpec
    session_summary: Optional[str]
    windowed_history_messages: WindowedMessages
    tool_scope_data: dict[str, Any]
    messages_for_llm: List[ChatMessage]
    chat_record_messages: List[ChatMessage]
    token_usage: int
    turn_suspension: TurnSuspension


# 运行时用 Any，避免 Pydantic 解析 SuspendedTurnContext 内的 TYPE_CHECKING 前向引用。
if TYPE_CHECKING:
    SuspendedContextField = SuspendedTurnContext
else:
    SuspendedContextField = Any


def _encode_context(value: SuspendedTurnContext) -> str:
    return base64.b64encode(
        pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    ).decode("ascii")


class SuspendedChat(Document):
    """未完成 Chat Turn 的临时恢复缓存"""
    session_id: str
    user_id: str
    context: SuspendedContextField
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("context", mode="before")
    @classmethod
    def decode_context(cls, value):
        if isinstance(value, SuspendedTurnContext):
            return value
        if isinstance(value, str):
            return pickle.loads(base64.b64decode(value.encode("ascii"), validate=True))
        return value

    @field_serializer("context")
    def encode_context(self, value: SuspendedTurnContext):
        return _encode_context(value)

    class Settings:
        name = "wisepen_suspended_chat"
        # Beanie 的 BSON Encoder 不会调用 Pydantic field_serializer。
        bson_encoders = {SuspendedTurnContext: _encode_context}
        indexes = [
            IndexModel([
                ("session_id", ASCENDING),
                ("user_id", ASCENDING),
            ]),
        ]

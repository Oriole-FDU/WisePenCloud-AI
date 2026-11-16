from datetime import datetime, timezone

from beanie import Document
from pydantic import BaseModel, Field
from typing import Literal
from pymongo import ASCENDING, IndexModel

from chat.domain.entities.message import ChatMessage


class ModelUsage(BaseModel):
    request_id: str
    model_id: str
    provider_id: str
    model_name: str
    token_usage: int | None
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    usage_source: str | None = None
    provenance: Literal["observed", "estimated", "unknown"]
    input_billing_ratio: str
    cached_input_billing_ratio: str
    output_billing_ratio: str
    billable_tokens: int
    billing_status: Literal["not_attempted", "confirmed", "failed", "unknown"] = "not_attempted"
    provider_usage_status: Literal["not_attempted", "confirmed", "unknown"] = "not_attempted"
    receipt: dict = Field(default_factory=dict)
    attempted_at: datetime | None = None


class SubagentRoot(Document):
    user_id: str
    session_id: str
    root_execution_id: str
    turn_id: str
    status: str = "running"
    sequence: int = 0
    total_runs: int = 0
    usage: list[ModelUsage] = Field(default_factory=list)

    class Settings:
        name = "wisepen_subagent_root"
        indexes = [
            IndexModel([("root_execution_id", ASCENDING)], unique=True),
            IndexModel([("user_id", ASCENDING), ("session_id", ASCENDING)]),
        ]


class SubagentRun(Document):
    """獨立集合，父聊天 assembler 不會載入這裡的訊息。"""

    user_id: str
    session_id: str
    root_execution_id: str
    turn_id: str | None = None
    child_id: str
    run_id: str
    parent_run_id: str | None = None
    depth: int = 1
    agent_id: str | None = None
    agent_version: int | None = None
    status: str = "running"
    output: str = ""
    error: str | None = None
    model_id: str
    provider_id: str
    token_usage: int = 0
    billing_status: str = "not_attempted"
    usage: list[ModelUsage] = Field(default_factory=list)
    history_available: bool = False
    messages: list[ChatMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "wisepen_subagent_run"
        indexes = [
            IndexModel([("run_id", ASCENDING)], unique=True),
            IndexModel([("user_id", ASCENDING), ("session_id", ASCENDING),
                        ("root_execution_id", ASCENDING), ("created_at", ASCENDING)]),
            IndexModel([("user_id", ASCENDING), ("session_id", ASCENDING),
                        ("child_id", ASCENDING), ("created_at", ASCENDING)]),
        ]

    def public_view(self):
        return self.model_dump(mode="json", include={
            "root_execution_id", "child_id", "run_id", "parent_run_id", "status",
            "output", "error", "model_id", "provider_id", "token_usage", "billing_status", "history_available",
            "agent_id", "agent_version", "depth",
            "usage",
        })

    def for_storage(self):
        saved = self.model_copy(deep=True)
        saved.messages = ChatMessage.for_persistence(saved.messages) if self.history_available else []
        if not self.history_available:
            saved.output = ""
            saved.error = None
        return saved

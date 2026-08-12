from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

class SandboxState(StrEnum):
    """沙箱生命周期状态。"""

    WARMING = "warming"
    READY = "ready"
    USER_ACTIVE = "user_active"
    RETIRING = "retiring"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    LOST = "lost"


class SandboxDocument(Document):
    """沙箱记录"""

    sandbox_id: str = Field(default_factory=lambda: uuid4().hex, description="沙箱 ID")
    container_id: str = Field(..., description="容器 ID")
    provider_id: str = Field(..., description="创建该沙箱的 provider ID")
    base_url: str | None = Field(default=None, description="沙箱服务基地址")

    metadata: dict[str, Any] = Field(default_factory=dict, description="沙箱附加元数据")

    state: SandboxState = Field(..., description="沙箱当前生命周期状态")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="沙箱创建时间")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="沙箱记录更新时间")
    bind_user_id: str | None = Field(default=None, description="当前绑定的用户 ID")
    bind_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="绑定发生时间")
    last_error: str | None = Field(default=None, description="最近一次错误信息")

    class Settings:
        name = "wisepen_sandbox_sandbox"
        indexes = [
            IndexModel([("sandbox_id", ASCENDING)], unique=True, name="uniq_sandbox_id"),
            IndexModel([("provider_id", ASCENDING)], name="idx_provider_id"),
            IndexModel([("state", ASCENDING), ("updated_at", ASCENDING)], name="idx_state_updated_at"),
            IndexModel(
                [("bind_user_id", ASCENDING), ("state", ASCENDING), ("updated_at", DESCENDING)],
                name="idx_bind_state_updated",
            ),
        ]


SANDBOX_ALLOWED_TRANSITIONS: dict[SandboxState, frozenset[SandboxState]] = {
    SandboxState.WARMING: frozenset({SandboxState.READY, SandboxState.DESTROYING}),
    SandboxState.READY: frozenset({SandboxState.USER_ACTIVE, SandboxState.DESTROYING}),
    SandboxState.USER_ACTIVE: frozenset({SandboxState.RETIRING, SandboxState.DESTROYING}),
    SandboxState.RETIRING: frozenset({SandboxState.DESTROYING}),
    SandboxState.DESTROYING: frozenset({SandboxState.DESTROYED, SandboxState.LOST}),
    SandboxState.DESTROYED: frozenset(),
    SandboxState.LOST: frozenset(),
}


def can_transition(expected: SandboxState, state: SandboxState) -> bool:
    """判断 expected -> state 是否为合法的沙箱状态迁移。"""
    return state in SANDBOX_ALLOWED_TRANSITIONS[expected]

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from beanie import Document
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING, IndexModel

class SandboxState(StrEnum):
    """沙箱生命周期状态。"""

    CREATING = "creating"
    WARMING = "warming"
    READY = "ready"
    USER_ACTIVE = "user_active"
    RETIRING = "retiring"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"
    LOST = "lost"


class SandboxEndpointRef(BaseModel):
    """沙箱对服务内部暴露的访问入口"""

    base_url: str = Field(..., description="沙箱内服务基地址")
    token: str | None = Field(default=None, description="访问令牌")


class SandboxDocument(Document):
    """沙箱记录"""

    sandbox_id: str = Field(..., description="沙箱 ID")
    provider_id: str = Field(..., description="创建该沙箱的 provider ID")
    endpoint: SandboxEndpointRef | None = Field(default=None, description="沙箱访问入口")

    metadata: dict[str, Any] = Field(default_factory=dict, description="沙箱附加元数据")

    state: SandboxState = Field(..., description="沙箱当前生命周期状态")
    created_at: datetime = Field(default=datetime.now(timezone.utc), description="沙箱创建时间")
    updated_at: datetime = Field(default=datetime.now(timezone.utc), description="沙箱记录更新时间")
    bind_user_id: str | None = Field(default=None, description="当前绑定的用户 ID")
    bind_at: datetime = Field(default=datetime.now(timezone.utc), description="绑定发生时间")
    last_error: str | None = Field(default=None, description="最近一次错误信息")

    class Settings:
        name = "wisepen_sandbox_v1_sandbox"
        indexes = [
            IndexModel([("sandbox_id", ASCENDING)], unique=True, name="uniq_sandbox_id"),
            IndexModel([("provider_id", ASCENDING)], name="idx_provider_id"),
            IndexModel([("state", ASCENDING), ("updated_at", ASCENDING)], name="idx_state_updated_at"),
            IndexModel(
                [("owner_user_id", ASCENDING), ("state", ASCENDING), ("updated_at", DESCENDING)],
                name="idx_owner_state_updated",
            ),
        ]


SANDBOX_ALLOWED_TRANSITIONS: dict[SandboxState, frozenset[SandboxState]] = {
    SandboxState.CREATING: frozenset({SandboxState.WARMING, SandboxState.DESTROYING}),
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

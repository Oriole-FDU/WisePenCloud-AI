from typing import Any

from pydantic import BaseModel, Field

from sandbox_v1.domain.entities.sandbox import SandboxState


class PoolSnapshot(BaseModel):
    """沙箱池在某一时刻的状态快照"""

    generation: int = Field(..., description="池快照版本号")
    counts: dict[SandboxState, int] = Field(..., description="各状态沙箱数量统计")
    empty_checkouts: int = Field(default=0, description="未能分配 READY 沙箱的累计次数")
    metrics: dict[str, Any] = Field(default_factory=dict, description="附加指标")
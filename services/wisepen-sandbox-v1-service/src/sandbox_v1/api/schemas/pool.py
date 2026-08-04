from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from sandbox_v1.domain.entities import PoolSnapshot


class PoolMetricsResponse(BaseModel):
    """沙箱池指标快照；核心字段固定，新增指标作为额外字段返回。"""

    model_config = ConfigDict(extra="allow")

    generation: int = Field(..., ge=0, description="Pool 状态代数。")
    empty_checkouts: int = Field(..., ge=0, description="无 READY 实例时的 checkout 次数。")
    min_ready: int = Field(..., ge=0, description="最低 READY 实例数。")
    target_ready: int = Field(..., ge=0, description="目标 READY 实例数。")

    @classmethod
    def from_snapshot(cls, snapshot: PoolSnapshot) -> "PoolMetricsResponse":
        return cls.model_validate(snapshot.as_dict())

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Protocol

from sandbox_v1.domain.entities import (
    PoolSnapshot,
    SandboxRecord,
    SandboxState,
)
from sandbox_v1.domain.interfaces.metrics import MetricsPort


class SandboxRepository(Protocol):
    """沙箱池记录和用户绑定的权威存储端口。"""

    @property
    def metrics(self) -> MetricsPort:
        """返回与 Repository 共享的指标端口。"""
        ...

    async def save(self, record: SandboxRecord) -> None:
        """保存或覆盖一条沙箱权威记录。"""
        ...

    async def get(self, sandbox_id: str) -> SandboxRecord | None:
        """按 sandbox_id 读取沙箱权威记录。"""
        ...

    async def records_in(self, states: Iterable[SandboxState]) -> list[SandboxRecord]:
        """读取处于指定状态集合内的记录。"""
        ...

    async def snapshot(self, *, min_ready: int = 0, target_ready: int = 0) -> PoolSnapshot:
        """生成包含池水位、状态计数和指标的快照。"""
        ...

    async def transition(
        self, sandbox_id: str, expected: SandboxState, state: SandboxState, *, error: str | None = None
    ) -> SandboxRecord:
        """仅当当前状态等于 expected 且目标状态合法时执行转换。"""
        ...

    async def checkout_ready(
        self,
        user_id: str,
        max_user_bindings: int = 20,
    ) -> SandboxRecord:
        """为用户分配 READY 容器，或复用用户已有绑定。"""
        ...

    async def records_older_than(self, state: SandboxState, cutoff: datetime) -> list[SandboxRecord]:
        """读取指定状态下 updated_at 不晚于 cutoff 的记录。"""
        ...

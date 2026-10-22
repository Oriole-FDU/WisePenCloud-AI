from __future__ import annotations

from datetime import datetime
from typing import Iterable, Protocol

from sandbox.domain.entities import (
    SandboxDocument,
    SandboxState,
)


class SandboxRepository(Protocol):
    """SandboxDocument 的 Mongo 权威仓储端口"""

    async def save(self, sandbox: SandboxDocument) -> None:
        """保存或覆盖一条沙箱记录"""
        ...

    async def get_by_id(self, sandbox_id: str) -> SandboxDocument | None:
        """按 sandbox_id 读取单条沙箱记录"""
        ...

    async def get_by_states(
        self,
        states: Iterable[SandboxState],
    ) -> list[SandboxDocument]:
        """读取处于指定状态集合内的沙箱记录"""
        ...

    async def count_by_state(self) -> dict[SandboxState, int]:
        """按生命周期状态聚合沙箱数量"""
        ...

    async def get_by_user_binding(
        self,
        user_id: str,
    ) -> SandboxDocument | None:
        """读取用户的 USER_ACTIVE 或 RETIRING 沙箱；回收导出期间不可另行分配"""
        ...

    async def assign_to_user(
        self,
        user_id: str,
    ) -> SandboxDocument:
        """为用户原子分配 READY 沙箱，设置分配时间但不记录实际活动"""
        ...

    async def register_active_command(
        self,
        sandbox_id: str,
        user_id: str,
        occurred_at: datetime,
    ) -> SandboxDocument | None:
        """记录沙箱活跃状态"""
        ...

    async def unregister_active_command(self, sandbox_id: str, count: int = 1) -> SandboxDocument | None:
        """解除沙箱活跃状态记录"""
        ...

    async def record_activity(
        self,
        sandbox_id: str,
        user_id: str,
        occurred_at: datetime,
    ) -> SandboxDocument | None:
        """仅在沙箱仍归属该用户且为 USER_ACTIVE 时更新实际活动时间。"""
        ...

    async def claim_idle_sandbox(self, sandbox_id: str, cutoff: datetime) -> SandboxDocument | None:
        """原子复查空闲条件并转入 RETIRING"""
        ...

    async def change_state(
        self,
        sandbox_id: str,
        state: SandboxState,
        *,
        clear_user_binding: bool = False,
    ) -> SandboxDocument | None:
        """更新 sandbox 状态，可选地清除用户绑定。"""
        ...

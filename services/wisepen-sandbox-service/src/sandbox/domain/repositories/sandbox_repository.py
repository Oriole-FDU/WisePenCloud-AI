from __future__ import annotations

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
        """读取指定用户当前绑定的沙箱记录"""
        ...

    async def assign_to_user(
        self,
        user_id: str,
    ) -> SandboxDocument:
        """为用户原子分配一个 READY 沙箱，或在容量/空池时抛出领域异常"""
        ...

    async def change_state(
        self,
        sandbox_id: str,
        state: SandboxState,
    ) -> SandboxDocument | None:
        """更新 sandbox 生命周期状态，并返回更新后的记录"""
        ...
from __future__ import annotations

from typing import Protocol

from sandbox.domain.entities import WorkspaceSnapshot


class WorkspaceStore(Protocol):
    async def snapshot(
        self, tenant_id: str, workspace_id: str
    ) -> WorkspaceSnapshot:
        ...

    async def commit(
        self,
        snapshot: WorkspaceSnapshot,
        lease_id: str,
        fencing_token: int = 0,
    ) -> None:
        ...

    async def delete(self, tenant_id: str, workspace_id: str) -> None:
        ...

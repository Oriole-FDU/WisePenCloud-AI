from __future__ import annotations

from typing import Protocol

from sandbox.domain.entities import SandboxRef, WorkspaceSnapshot


class FileTransferPort(Protocol):
    """Runtime workspace transfer boundary.

    Implementations may use docker cp, an SDK, or another transport. The
    application layer only exchanges complete, tenant-scoped snapshots.
    """

    async def copy_in(self, sandbox: SandboxRef, snapshot: WorkspaceSnapshot) -> None:
        ...

    async def copy_out(
        self, sandbox: SandboxRef, tenant_id: str, workspace_id: str
    ) -> WorkspaceSnapshot:
        ...

    async def checkpoint(
        self,
        sandbox: SandboxRef,
        tenant_id: str,
        workspace_id: str,
        lease_id: str,
        fencing_token: int,
    ) -> WorkspaceSnapshot:
        ...

    async def delete_workspace(
        self, sandbox: SandboxRef, tenant_id: str, workspace_id: str
    ) -> None:
        ...

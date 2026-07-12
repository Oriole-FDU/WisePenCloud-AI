from __future__ import annotations

from typing import Protocol

from sandbox.models import (
    Endpoint,
    ExecutionRequest,
    ExecutionResult,
    Health,
    SandboxLease,
    SandboxRef,
    SandboxSpec,
    WorkspaceSnapshot,
)


class SandboxProvider(Protocol):
    async def create(self, spec: SandboxSpec) -> SandboxRef:
        ...

    async def wait_ready(self, sandbox: SandboxRef, timeout_seconds: float) -> Health:
        ...

    async def health(self, sandbox: SandboxRef) -> Health:
        ...

    async def prepare_workspace(
        self, sandbox: SandboxRef, workspace: WorkspaceSnapshot
    ) -> None:
        ...

    async def activate(self, sandbox: SandboxRef, lease: SandboxLease) -> Endpoint:
        ...

    async def forward(
        self, sandbox: SandboxRef, request: ExecutionRequest
    ) -> ExecutionResult:
        ...

    async def export_workspace(
        self, sandbox: SandboxRef, tenant_id: str, workspace_id: str
    ) -> WorkspaceSnapshot:
        ...

    async def destroy(self, sandbox: SandboxRef, reason: str) -> None:
        ...


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


class LeaderLease(Protocol):
    async def acquire(self, key: str, owner: str, ttl_seconds: float) -> bool:
        ...

    async def release(self, key: str, owner: str) -> None:
        ...

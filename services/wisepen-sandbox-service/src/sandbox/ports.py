from __future__ import annotations

from typing import Protocol

from sandbox.models import (
    Endpoint,
    ExecutionRequest,
    ExecutionResult,
    SandboxLease,
    SandboxRef,
    SandboxSpec,
    WorkspaceSnapshot,
)


class SandboxProvider(Protocol):
    async def create(self, spec: SandboxSpec) -> SandboxRef:
        ...

    async def wait_ready(self, sandbox: SandboxRef, timeout_seconds: float) -> None:
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

    async def commit(self, snapshot: WorkspaceSnapshot, lease_id: str) -> None:
        ...

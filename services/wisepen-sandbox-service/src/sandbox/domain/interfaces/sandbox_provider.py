from __future__ import annotations

from typing import Protocol

from sandbox.domain.entities import (
    DiscoveredSandbox,
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
    async def validate_deployment(self) -> None:
        ...

    async def create(self, spec: SandboxSpec) -> SandboxRef:
        ...

    async def wait_ready(self, sandbox: SandboxRef, timeout_seconds: float) -> Health:
        ...

    async def health(self, sandbox: SandboxRef) -> Health:
        ...

    async def list_managed(self) -> list[DiscoveredSandbox]:
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

    async def checkpoint_workspace(
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

    async def destroy(self, sandbox: SandboxRef, reason: str) -> None:
        ...

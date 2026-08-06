from __future__ import annotations

from typing import Protocol

from sandbox_v1.domain.entities import (
    DiscoveredSandbox,
    Health,
    SandboxRef,
    SandboxSpec,
)


class SandboxProvider(Protocol):
    """核心容器生命周期端口。

    v1 临时服务只需要创建、健康检查、启动发现和销毁。File/Process/Browser
    会在对应阶段用新的 capability 端口接入，不沿用旧通用 forward 协议。
    """

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

    async def cleanup_owned(self) -> int:
        ...

    async def destroy(self, sandbox: SandboxRef, reason: str) -> None:
        ...

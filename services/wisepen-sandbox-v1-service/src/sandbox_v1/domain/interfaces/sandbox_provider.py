from __future__ import annotations

from typing import Protocol

from sandbox_v1.domain.entities import (
    DiscoveredSandbox,
    Health,
    SandboxRef,
    SandboxSpec,
)


class SandboxProvider(Protocol):
    """核心容器生命周期端口。"""

    async def validate_deployment(self) -> None:
        """启动前校验当前 provider 部署是否可用。"""
        ...

    async def create(self, spec: SandboxSpec) -> SandboxRef:
        """按 provider-neutral spec 创建一个沙箱容器，并返回引用。"""
        ...

    async def wait_ready(self, sandbox: SandboxRef, timeout_seconds: float) -> Health:
        """等待容器进入 ready 状态，返回等待过程中的健康结果。"""
        ...

    async def health(self, sandbox: SandboxRef) -> Health:
        """对已存在容器执行一次即时健康检查。"""
        ...

    async def list_managed(self) -> list[DiscoveredSandbox]:
        """发现当前 provider 中由本服务管理的候选容器。"""
        ...

    async def cleanup_owned(self) -> int:
        """停机时清理本进程拥有的 provider 资源，并返回清理数量。"""
        ...

    async def destroy(self, sandbox: SandboxRef, reason: str) -> None:
        """按原因销毁指定沙箱容器。"""
        ...

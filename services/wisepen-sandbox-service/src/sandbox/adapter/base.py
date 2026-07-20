"""
沙箱适配器基础接口。

所有沙箱后端（AIO Docker、K8s Pod、本地进程等）通过实现此 Protocol 接入。
与 queue_jurfal/ports.py 的 SandboxProvider 等价，但作为 sandbox-service 的一部分。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from sandbox.queue_jurfal.models import (
    SandboxRef, SandboxSpec, Health, Endpoint,
    SandboxLease, WorkspaceSnapshot, ExecutionRequest, ExecutionResult,
)


@runtime_checkable
class SandboxAdapter(Protocol):
    """沙箱后端适配器协议 — 8 个生命周期方法。"""

    async def create(self, spec: SandboxSpec) -> SandboxRef:
        """创建一个新的沙箱实例。"""
        ...

    async def wait_ready(self, sandbox: SandboxRef,
                         timeout_seconds: float) -> Health:
        """等待沙箱就绪。"""
        ...

    async def health(self, sandbox: SandboxRef) -> Health:
        """检查沙箱健康状态。"""
        ...

    async def prepare_workspace(self, sandbox: SandboxRef,
                                workspace: WorkspaceSnapshot) -> None:
        """将工作空间文件部署到沙箱中。"""
        ...

    async def activate(self, sandbox: SandboxRef,
                       lease: SandboxLease) -> Endpoint:
        """激活沙箱，返回连接端点（含 VNC URL 等）。"""
        ...

    async def forward(self, sandbox: SandboxRef,
                      request: ExecutionRequest) -> ExecutionResult:
        """向沙箱转发执行请求（文件读写、Shell 等）。"""
        ...

    async def export_workspace(self, sandbox: SandboxRef,
                               tenant_id: str,
                               workspace_id: str) -> WorkspaceSnapshot:
        """从沙箱导出当前工作空间文件。"""
        ...

    async def destroy(self, sandbox: SandboxRef, reason: str) -> None:
        """销毁沙箱实例。"""
        ...

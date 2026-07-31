from __future__ import annotations

import asyncio

from sandbox.domain.entities import (
    Endpoint,
    ExecutionRequest,
    ExecutionResult,
    Health,
    SandboxLease,
    SandboxRef,
    SandboxSpec,
    WorkspaceSnapshot,
)
from sandbox.domain.interfaces.sandbox_provider import SandboxProvider
from sandbox.domain.interfaces.file_transfer import FileTransferPort

from sandbox.core.providers.aio_adapter.client import AioClient
from sandbox.core.providers.aio_adapter.docker_runtime import DockerRuntime
from sandbox.core.providers.aio_adapter.models import AdapterConfig
from sandbox.core.providers.aio_adapter.path_policy import PathPolicy, TenantScope


class AioSandboxProvider(SandboxProvider):
    """SandboxProvider 到 all-in-one-sandbox 的协议适配器。"""

    def __init__(
        self,
        runtime: DockerRuntime,
        file_transfer: FileTransferPort,
        *,
        request_timeout_seconds: float = 30.0,
    ) -> None:
        self._runtime = runtime
        self._file_transfer = file_transfer
        self._request_timeout = request_timeout_seconds
        self._clients: dict[str, AioClient] = {}

    @classmethod
    def from_settings(
        cls,
        config: AdapterConfig,
        file_transfer: FileTransferPort,
    ) -> "AioSandboxProvider":
        return cls(
            DockerRuntime(config),
            file_transfer,
            request_timeout_seconds=config.request_timeout_seconds,
        )

    async def validate_deployment(self) -> None:
        await asyncio.to_thread(self._runtime.validate_deployment)

    async def create(self, spec: SandboxSpec) -> SandboxRef:
        handle = await asyncio.to_thread(self._runtime.create, spec)
        return SandboxRef(
            sandbox_id=f"sb_{handle.container_id[:16]}",
            provider_id=handle.container_id,
            endpoint=Endpoint(
                handle.endpoint,
                public_vnc_url=handle.public_vnc_url,
                public_websocket_url=handle.public_websocket_url,
            ),
            metadata={"image": spec.image},
        )

    async def wait_ready(self, sandbox: SandboxRef, timeout_seconds: float) -> Health:
        client = self._client(sandbox)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        # 容器启动后 AIO HTTP 服务会延迟可用，预热阶段轮询健康接口。
        while asyncio.get_running_loop().time() < deadline:
            if await client.health():
                return Health(True, "ready")
            await asyncio.sleep(1)
        raise TimeoutError(f"沙箱 {sandbox.sandbox_id} 未在限定时间内就绪")

    async def health(self, sandbox: SandboxRef) -> Health:
        healthy = await self._client(sandbox).health()
        return Health(healthy, "ready" if healthy else "unhealthy")

    async def prepare_workspace(
        self, sandbox: SandboxRef, workspace: WorkspaceSnapshot
    ) -> None:
        await self._file_transfer.copy_in(sandbox, workspace)

    async def activate(self, sandbox: SandboxRef, lease: SandboxLease) -> Endpoint:
        await self._client(sandbox).health()
        if sandbox.endpoint is None:
            raise RuntimeError("沙箱缺少 endpoint")
        return sandbox.endpoint

    async def forward(
        self, sandbox: SandboxRef, request: ExecutionRequest
    ) -> ExecutionResult:
        client = self._client(sandbox)
        policy = PathPolicy(
            TenantScope(request.tenant_id, request.workspace_id),
            self._runtime.workdir,
            isolate_scope=True,
        )
        payload = request.payload
        operation = request.operation
        # 这里是内部统一操作名到 AIO HTTP API 的唯一映射层；上层不应感知 AIO 字段名。
        if operation == "read_file":
            data = await client.file_read(
                policy.translate(str(payload.get("file", ""))), payload.get("max_chars")
            )
        elif operation == "write_file":
            data = await client.file_write(
                policy.translate(str(payload.get("file", ""))),
                str(payload.get("content", "")),
            )
        elif operation == "list_directory":
            data = await client.file_list(
                policy.translate(str(payload.get("path", "."))),
                bool(payload.get("recursive", False)),
            )
        elif operation == "grep_files":
            data = await client.file_grep(
                policy.translate(str(payload.get("path", "."))),
                str(payload.get("pattern", "")),
                bool(payload.get("recursive", True)),
                bool(payload.get("ignore_case", False)),
            )
        elif operation == "edit_file":
            data = await client.file_replace(
                policy.translate(str(payload.get("file", ""))),
                str(payload.get("old_str", "")),
                str(payload.get("new_str", "")),
            )
        elif operation == "shell_exec":
            data = await client.shell_exec(
                str(payload.get("command", "")),
                policy.translate(str(payload.get("exec_dir", "."))),
                int(payload.get("timeout_ms", 30000)),
            )
        elif operation == "execute":
            data = await client.code_execute(
                str(payload.get("language", "python")),
                str(payload.get("code", "")),
                payload,
            )
        else:
            raise ValueError(f"不支持的沙箱操作：{operation}")
        return ExecutionResult(request.request_id, "succeeded", data)

    async def export_workspace(
        self, sandbox: SandboxRef, tenant_id: str, workspace_id: str
    ) -> WorkspaceSnapshot:
        return await self._file_transfer.copy_out(
            sandbox, tenant_id, workspace_id
        )

    async def checkpoint_workspace(
        self,
        sandbox: SandboxRef,
        tenant_id: str,
        workspace_id: str,
        lease_id: str,
        fencing_token: int,
    ) -> WorkspaceSnapshot:
        return await self._file_transfer.checkpoint(
            sandbox,
            tenant_id,
            workspace_id,
            lease_id,
            fencing_token,
        )

    async def destroy(self, sandbox: SandboxRef, reason: str) -> None:
        await asyncio.to_thread(self._runtime.remove, sandbox.provider_id)
        self._clients.pop(sandbox.sandbox_id, None)

    async def cleanup_owned(self) -> int:
        return await asyncio.to_thread(self._runtime.cleanup_owned)

    def _client(self, sandbox: SandboxRef) -> AioClient:
        if sandbox.endpoint is None:
            raise RuntimeError("沙箱缺少 endpoint")
        client = self._clients.get(sandbox.sandbox_id)
        if client is None:
            client = AioClient(
                sandbox.endpoint.base_url,
                self._request_timeout,
                sandbox.endpoint.token,
            )
            self._clients[sandbox.sandbox_id] = client
        return client

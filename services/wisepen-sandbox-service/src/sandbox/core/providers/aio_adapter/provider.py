from __future__ import annotations

import asyncio
import shlex
from typing import Any

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
from sandbox.domain.execution_timeout import (
    DEFAULT_EXECUTION_TIMEOUT_MS,
    MAX_EXECUTION_TIMEOUT_MS,
    normalize_execution_timeout_ms,
)

from sandbox.core.providers.aio_adapter.client import AioClient
from sandbox.core.providers.aio_adapter.docker_runtime import DockerRuntime
from sandbox.core.providers.aio_adapter.models import AdapterConfig
from sandbox.core.providers.aio_adapter.path_policy import PathPolicy, TenantScope
from common.core.exceptions import ServiceException
from common.logger import error, info, warn


class AioSandboxProvider(SandboxProvider):
    """SandboxProvider 到 all-in-one-sandbox 的协议适配器。"""

    def __init__(
        self,
        runtime: DockerRuntime,
        file_transfer: FileTransferPort,
        *,
        request_timeout_seconds: float = 30.0,
        health_timeout_seconds: float = 3.0,
        health_retry_interval_seconds: float = 0.5,
        workspace_root: str = "/home/gem/workspaces",
        execution_default_timeout_ms: int = DEFAULT_EXECUTION_TIMEOUT_MS,
        execution_max_timeout_ms: int = MAX_EXECUTION_TIMEOUT_MS,
        execution_transport_grace_seconds: float = 5.0,
    ) -> None:
        self._runtime = runtime
        self._file_transfer = file_transfer
        self._request_timeout = request_timeout_seconds
        self._health_timeout = health_timeout_seconds
        self._health_retry_interval = health_retry_interval_seconds
        self._workspace_root = workspace_root.rstrip("/")
        self._execution_default_timeout_ms = execution_default_timeout_ms
        self._execution_max_timeout_ms = execution_max_timeout_ms
        self._execution_transport_grace_seconds = execution_transport_grace_seconds
        self._clients: dict[str, AioClient] = {}

    @classmethod
    def from_settings(
        cls,
        config: AdapterConfig,
        file_transfer: FileTransferPort,
    ) -> "AioSandboxProvider":

        info(
            "AIO 沙箱 provider 配置完成",
            docker_bin=config.docker_bin,
            image=config.image,
            docker_host=config.host,
            aio_port=config.api_port,
            file_transfer_port=file_transfer,
            network=config.network,
            request_timeout_seconds=config.request_timeout_seconds,
            warmup_timeout_seconds=config.warmup_timeout_seconds,
            health_timeout_seconds=config.health_timeout_seconds,
            health_retry_interval_seconds=config.health_retry_interval_seconds,
            workspace_root=config.workspace_root,
            execution_default_timeout_ms=config.execution_default_timeout_ms,
            execution_max_timeout_ms=config.execution_max_timeout_ms,
            execution_transport_grace_seconds=config.execution_transport_grace_seconds,
            create_max_attempts=config.create_max_attempts,
            create_retry_backoff_seconds=config.create_retry_backoff_seconds,
            e2e_label=config.e2e_label,
            tty=config.tty,
        )
        return cls(
            DockerRuntime(config),
            file_transfer,
            request_timeout_seconds=config.request_timeout_seconds,
            health_timeout_seconds=config.health_timeout_seconds,
            health_retry_interval_seconds=config.health_retry_interval_seconds,
            workspace_root=config.workspace_root,
            execution_default_timeout_ms=config.execution_default_timeout_ms,
            execution_max_timeout_ms=config.execution_max_timeout_ms,
            execution_transport_grace_seconds=config.execution_transport_grace_seconds,
        )

    async def validate_deployment(self) -> None:
        await asyncio.to_thread(self._runtime.validate_deployment)

    async def create(self, spec: SandboxSpec) -> SandboxRef:
        info(
            "AIO provider 开始创建沙箱",
            image=spec.image,
            cpu_cores=spec.cpu_cores,
            memory_mb=spec.memory_mb,
            environment_keys=sorted(spec.environment),
        )
        handle = await asyncio.to_thread(self._runtime.create, spec)
        ref = SandboxRef(
            sandbox_id=f"sb_{handle.container_id[:16]}",
            provider_id=handle.container_id,
            endpoint=Endpoint(
                handle.endpoint,
                public_vnc_url=handle.public_vnc_url,
                public_websocket_url=handle.public_websocket_url,
            ),
            metadata={"image": spec.image},
        )
        info(
            "AIO provider 创建沙箱返回",
            sandbox_id=ref.sandbox_id,
            provider_id=ref.provider_id,
            endpoint=ref.endpoint.base_url if ref.endpoint else None,
            image=spec.image,
        )
        return ref

    async def wait_ready(self, sandbox: SandboxRef, timeout_seconds: float) -> Health:
        client = self._client(sandbox)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        attempt = 0
        info(
            "AIO provider 开始等待沙箱就绪",
            sandbox_id=sandbox.sandbox_id,
            provider_id=sandbox.provider_id,
            endpoint=sandbox.endpoint.base_url if sandbox.endpoint else None,
            timeout_seconds=timeout_seconds,
        )
        # 容器启动后 AIO HTTP 服务会延迟可用，预热阶段轮询健康接口。
        while asyncio.get_running_loop().time() < deadline:
            attempt += 1
            try:
                remaining_seconds = deadline - asyncio.get_running_loop().time()
                healthy = await client.health(
                    timeout_seconds=min(self._health_timeout, remaining_seconds)
                )
            except ServiceException:
                remaining_seconds = max(
                    0, deadline - asyncio.get_running_loop().time()
                )
                await asyncio.sleep(min(self._health_retry_interval, remaining_seconds))
                continue
            if healthy:
                info(
                    "AIO provider 检查到沙箱已就绪",
                    sandbox_id=sandbox.sandbox_id,
                    provider_id=sandbox.provider_id,
                    endpoint=sandbox.endpoint.base_url if sandbox.endpoint else None,
                    attempt=attempt,
                )
                return Health(True, "ready", attempts=attempt)
            warn(
                "AIO provider 健康检查返回未就绪",
                sandbox_id=sandbox.sandbox_id,
                provider_id=sandbox.provider_id,
                endpoint=sandbox.endpoint.base_url if sandbox.endpoint else None,
                attempt=attempt,
            )
            remaining_seconds = max(0, deadline - asyncio.get_running_loop().time())
            await asyncio.sleep(min(self._health_retry_interval, remaining_seconds))
        exc = TimeoutError(f"沙箱 {sandbox.sandbox_id} 未在限定时间内就绪")
        error(
            "AIO provider 等待沙箱就绪超时",
            exc=exc,
            sandbox_id=sandbox.sandbox_id,
            provider_id=sandbox.provider_id,
            endpoint=sandbox.endpoint.base_url if sandbox.endpoint else None,
            attempts=attempt,
            timeout_seconds=timeout_seconds,
        )
        raise exc

    async def health(self, sandbox: SandboxRef) -> Health:
        info(
            "AIO provider 开始执行沙箱健康复检",
            sandbox_id=sandbox.sandbox_id,
            provider_id=sandbox.provider_id,
            endpoint=sandbox.endpoint.base_url if sandbox.endpoint else None,
        )
        healthy = await self._client(sandbox).health()
        info(
            "AIO provider 沙箱健康复检结束",
            sandbox_id=sandbox.sandbox_id,
            provider_id=sandbox.provider_id,
            healthy=healthy,
        )
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
            self._workspace_root,
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
            exec_dir = policy.translate(str(payload.get("exec_dir", ".")))
            timeout_ms = self._normalize_execution_timeout(payload.get("timeout_ms"))
            data = await client.shell_exec(
                self._shell_command(exec_dir, str(payload.get("command", ""))),
                exec_dir,
                timeout_ms,
                self._execution_transport_grace_seconds,
            )
        elif operation == "execute":
            language = str(payload.get("language", "python"))
            code = str(payload.get("code", ""))
            timeout_ms = self._normalize_execution_timeout(payload.get("timeout_ms"))
            command = self._script_command(language, code)
            if command is None:
                data = await client.code_execute(
                    language,
                    code,
                    timeout_ms,
                    self._execution_transport_grace_seconds,
                )
            else:
                # AIO code execution has no cancellation endpoint and may leave child
                # processes behind. Shell sessions expose /v1/shell/kill, which the
                # client invokes on timeout and which terminates the process tree.
                data = await client.shell_exec(
                    command,
                    policy.root,
                    timeout_ms,
                    self._execution_transport_grace_seconds,
                )
                data.setdefault("stdout", data.get("output"))
        else:
            raise ValueError(f"不支持的沙箱操作：{operation}")
        return ExecutionResult(request.request_id, "succeeded", data)

    @staticmethod
    def _shell_command(exec_dir: str, command: str) -> str:
        return f"cd -- {shlex.quote(exec_dir)} && {command}"

    @staticmethod
    def _script_command(language: str, code: str) -> str | None:
        normalized = language.strip().lower()
        quoted = shlex.quote(code)
        if normalized in {"py", "python", "python3"}:
            return f"python3 -c {quoted}"
        if normalized in {"js", "javascript", "node", "nodejs"}:
            return f"node -e {quoted}"
        if normalized in {"bash", "sh", "shell"}:
            return f"bash -c {quoted}"
        return None

    def _normalize_execution_timeout(self, value: Any) -> int:
        return normalize_execution_timeout_ms(
            value,
            default_timeout_ms=self._execution_default_timeout_ms,
            max_timeout_ms=self._execution_max_timeout_ms,
        )

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

    async def delete_workspace(
        self, sandbox: SandboxRef, tenant_id: str, workspace_id: str
    ) -> None:
        await self._file_transfer.delete_workspace(sandbox, tenant_id, workspace_id)

    async def destroy(self, sandbox: SandboxRef, reason: str) -> None:
        info(
            "AIO provider 开始销毁沙箱",
            sandbox_id=sandbox.sandbox_id,
            provider_id=sandbox.provider_id,
            endpoint=sandbox.endpoint.base_url if sandbox.endpoint else None,
            reason=reason,
        )
        try:
            await asyncio.to_thread(self._runtime.remove, sandbox.provider_id)
        except Exception as exc:
            error(
                "AIO provider 销毁沙箱失败",
                exc=exc,
                sandbox_id=sandbox.sandbox_id,
                provider_id=sandbox.provider_id,
                reason=reason,
            )
            raise
        self._clients.pop(sandbox.sandbox_id, None)
        info(
            "AIO provider 销毁沙箱完成",
            sandbox_id=sandbox.sandbox_id,
            provider_id=sandbox.provider_id,
            reason=reason,
        )

    async def cleanup_owned(self) -> int:
        return await asyncio.to_thread(self._runtime.cleanup_owned)

    def _client(self, sandbox: SandboxRef) -> AioClient:
        if sandbox.endpoint is None:
            raise RuntimeError("沙箱缺少 endpoint")
        client = self._clients.get(sandbox.sandbox_id)
        if client is None:
            info(
                "AIO provider 创建 HTTP client",
                sandbox_id=sandbox.sandbox_id,
                endpoint=sandbox.endpoint.base_url,
                timeout_seconds=self._request_timeout,
                has_token=sandbox.endpoint.token is not None,
            )
            client = AioClient(
                sandbox.endpoint.base_url,
                self._request_timeout,
                sandbox.endpoint.token,
            )
            self._clients[sandbox.sandbox_id] = client
        return client

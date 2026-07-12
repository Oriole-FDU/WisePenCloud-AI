from __future__ import annotations

import asyncio
import os

from sandbox.models import (
    Endpoint,
    ExecutionRequest,
    ExecutionResult,
    SandboxLease,
    SandboxRef,
    SandboxSpec,
    WorkspaceSnapshot,
)
from sandbox.ports import SandboxProvider

from aio_adapter.client import AioClient
from aio_adapter.docker_runtime import DockerRuntime
from aio_adapter.models import AdapterConfig
from aio_adapter.path_policy import PathPolicy, TenantScope


class AioSandboxProvider(SandboxProvider):
    def __init__(
        self,
        runtime: DockerRuntime,
        *,
        request_timeout_seconds: float = 30.0,
    ) -> None:
        self._runtime = runtime
        self._request_timeout = request_timeout_seconds
        self._clients: dict[str, AioClient] = {}

    @classmethod
    def from_environment(cls) -> "AioSandboxProvider":
        config = AdapterConfig(
            docker_bin=os.getenv("SANDBOX_DOCKER_BIN", "docker"),
            image=os.getenv("SANDBOX_IMAGE", "ghcr.io/agent-infra/sandbox:latest"),
            host=os.getenv("SANDBOX_DOCKER_HOST", "127.0.0.1"),
            api_port=int(os.getenv("SANDBOX_AIO_PORT", "8080")),
            network=os.getenv("SANDBOX_DOCKER_NETWORK") or None,
            request_timeout_seconds=float(
                os.getenv("SANDBOX_REQUEST_TIMEOUT_SECONDS", "30")
            ),
            warmup_timeout_seconds=float(
                os.getenv("SANDBOX_WARMUP_TIMEOUT_SECONDS", "60")
            ),
        )
        return cls(DockerRuntime(config), request_timeout_seconds=config.request_timeout_seconds)

    async def create(self, spec: SandboxSpec) -> SandboxRef:
        handle = await asyncio.to_thread(self._runtime.create, spec)
        return SandboxRef(
            sandbox_id=f"sb_{handle.container_id[:16]}",
            provider_id=handle.container_id,
            endpoint=Endpoint(handle.endpoint),
            metadata={"image": spec.image},
        )

    async def wait_ready(self, sandbox: SandboxRef, timeout_seconds: float) -> None:
        client = self._client(sandbox)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if await client.health():
                return
            await asyncio.sleep(1)
        raise TimeoutError(f"sandbox {sandbox.sandbox_id} did not become ready")

    async def prepare_workspace(
        self, sandbox: SandboxRef, workspace: WorkspaceSnapshot
    ) -> None:
        client = self._client(sandbox)
        policy = PathPolicy(TenantScope(workspace.tenant_id, workspace.workspace_id))
        for path, content in workspace.files.items():
            await client.file_write(policy.translate(path), content)

    async def activate(self, sandbox: SandboxRef, lease: SandboxLease) -> Endpoint:
        await self._client(sandbox).health()
        if sandbox.endpoint is None:
            raise RuntimeError("sandbox has no endpoint")
        return sandbox.endpoint

    async def forward(
        self, sandbox: SandboxRef, request: ExecutionRequest
    ) -> ExecutionResult:
        client = self._client(sandbox)
        policy = PathPolicy(TenantScope(request.tenant_id, request.workspace_id))
        payload = request.payload
        operation = request.operation
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
                policy.translate(str(payload.get("path", "/workspace"))),
                bool(payload.get("recursive", False)),
            )
        elif operation == "grep_files":
            data = await client.file_grep(
                policy.translate(str(payload.get("path", "/workspace"))),
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
                policy.translate(str(payload.get("exec_dir", "/workspace"))),
                int(payload.get("timeout_ms", 30000)),
            )
        elif operation == "execute":
            data = await client.request("/v1/code/execute", payload)
        else:
            raise ValueError(f"unsupported sandbox operation: {operation}")
        return ExecutionResult(request.request_id, "succeeded", data)

    async def export_workspace(
        self, sandbox: SandboxRef, tenant_id: str, workspace_id: str
    ) -> WorkspaceSnapshot:
        client = self._client(sandbox)
        policy = PathPolicy(TenantScope(tenant_id, workspace_id))
        listing = await client.file_list("/workspace", recursive=True)
        files: dict[str, str] = {}
        entries = listing.get("files", []) if isinstance(listing, dict) else []
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("is_directory"):
                continue
            path = str(entry.get("path") or entry.get("name") or "")
            virtual = policy.reverse(path)
            content = await client.file_read(policy.translate(virtual))
            files[virtual.removeprefix("/workspace/")] = str(
                content.get("content", "")
            )
        return WorkspaceSnapshot(tenant_id, workspace_id, files)

    async def destroy(self, sandbox: SandboxRef, reason: str) -> None:
        await asyncio.to_thread(self._runtime.remove, sandbox.provider_id)
        self._clients.pop(sandbox.sandbox_id, None)

    def _client(self, sandbox: SandboxRef) -> AioClient:
        if sandbox.endpoint is None:
            raise RuntimeError("sandbox has no endpoint")
        client = self._clients.get(sandbox.sandbox_id)
        if client is None:
            client = AioClient(sandbox.endpoint.base_url, self._request_timeout)
            self._clients[sandbox.sandbox_id] = client
        return client

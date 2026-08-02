from __future__ import annotations

import uuid
from typing import Any

from common.core.exceptions import ServiceException
from common.security.context import SecurityContextHolder

from sandbox.application.services.sandbox_scheduler import SandboxScheduler
from sandbox.domain.entities import ExecutionRequest, SandboxLease
from sandbox.domain.error_codes import SandboxErrorCode
from sandbox.domain.execution_timeout import (
    DEFAULT_EXECUTION_TIMEOUT_MS,
    MAX_EXECUTION_TIMEOUT_MS,
    normalize_execution_timeout_ms,
)


class SandboxSessionService:
    """把可信用户/会话上下文绑定到 Scheduler 租约 API。"""

    def __init__(
        self,
        scheduler: SandboxScheduler,
        *,
        execution_default_timeout_ms: int = DEFAULT_EXECUTION_TIMEOUT_MS,
        execution_max_timeout_ms: int = MAX_EXECUTION_TIMEOUT_MS,
    ) -> None:
        self._scheduler = scheduler
        self._execution_default_timeout_ms = execution_default_timeout_ms
        self._execution_max_timeout_ms = execution_max_timeout_ms

    async def execute(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload = dict(payload)
        if operation in {"execute", "shell_exec"}:
            payload["timeout_ms"] = normalize_execution_timeout_ms(
                payload.get("timeout_ms"),
                default_timeout_ms=self._execution_default_timeout_ms,
                max_timeout_ms=self._execution_max_timeout_ms,
            )
        lease = await self._allocate()
        # 同一 MCP/VNC 会话复用 request_id，但每次 execute 仍生成唯一子请求，便于追踪。
        request_id = f"{lease.request_id}:{uuid.uuid4().hex}"
        result = await self._scheduler.execute(
            lease.lease_id,
            ExecutionRequest(
                request_id=request_id,
                tenant_id=lease.tenant_id,
                workspace_id=lease.workspace_id,
                operation=operation,
                payload=payload,
                fencing_token=lease.fencing_token,
            ),
        )
        return {
            **result.data,
            "request_id": result.request_id,
            "sandbox_id": lease.sandbox_id,
            "status": result.status,
        }

    async def acquire(self) -> SandboxLease:
        return await self._allocate()

    async def acquire_for(self, tenant_id: str, workspace_id: str) -> SandboxLease:
        return await self._allocate(tenant_id, workspace_id)

    async def release(self) -> None:
        tenant_id, workspace_id, request_id = self._context()
        await self._scheduler.release_request(
            request_id or self._default_request_id(tenant_id, workspace_id),
            tenant_id,
            workspace_id,
        )

    async def release_for(self, tenant_id: str, workspace_id: str) -> None:
        await self._scheduler.release_request(
            self._default_request_id(tenant_id, workspace_id),
            tenant_id,
            workspace_id,
        )

    def _context(self) -> tuple[str, str, str | None]:
        # 安全上下文来自网关安全中间件，不能信任用户在工具参数中传身份。
        tenant_id = (SecurityContextHolder.get_user_id() or "").strip()
        workspace_id = (SecurityContextHolder.get_session_id() or "").strip()
        if not tenant_id or not workspace_id:
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "用户和会话上下文不能为空",
            )
        return tenant_id, workspace_id, SecurityContextHolder.get_request_id()

    async def _allocate(
        self,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> SandboxLease:
        request_id = None
        if not tenant_id or not workspace_id:
            tenant_id, workspace_id, request_id = self._context()
        # 工具协议和远程桌面是长连接式入口，按 tenant/workspace 固定 request_id 复用同一租约。
        request_id = request_id or self._default_request_id(tenant_id, workspace_id)
        return await self._scheduler.allocate(request_id, tenant_id, workspace_id)

    @staticmethod
    def _default_request_id(tenant_id: str, workspace_id: str) -> str:
        return f"mcp:{tenant_id}:{workspace_id}"

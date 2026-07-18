from __future__ import annotations

import uuid
from typing import Any

from common.core.exceptions import ServiceException
from common.security.context import SecurityContextHolder

from sandbox.application.services.sandbox_scheduler import SandboxScheduler
from sandbox.domain.entities import ExecutionRequest, SandboxLease
from sandbox.domain.error_codes import SandboxErrorCode


class SandboxSessionService:
    """Bind trusted user/session context to the scheduler lease API."""

    def __init__(self, scheduler: SandboxScheduler) -> None:
        self._scheduler = scheduler

    async def execute(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        lease = await self._allocate()
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
        lease = await self._allocate()
        await self._scheduler.release(lease.lease_id, lease.fencing_token)

    async def release_for(self, tenant_id: str, workspace_id: str) -> None:
        lease = await self._allocate(tenant_id, workspace_id)
        await self._scheduler.release(lease.lease_id, lease.fencing_token)

    def _context(self) -> tuple[str, str]:
        tenant_id = (SecurityContextHolder.get_user_id() or "").strip()
        workspace_id = (SecurityContextHolder.get_session_id() or "").strip()
        if not tenant_id or not workspace_id:
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "user and session context are required",
            )
        return tenant_id, workspace_id

    async def _allocate(
        self,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> SandboxLease:
        if not tenant_id or not workspace_id:
            tenant_id, workspace_id = self._context()
        request_id = f"mcp:{tenant_id}:{workspace_id}"
        return await self._scheduler.allocate(request_id, tenant_id, workspace_id)

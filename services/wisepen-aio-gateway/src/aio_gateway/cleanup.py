"""
Periodic workspace cleanup for AIO Gateway.

Tracks access via .last_access marker files inside each tenant workspace.
Scans and deletes workspaces that have been inactive for TTL seconds.
All operations go through AIO's shell/exec API (gateway cannot access AIO filesystem directly).
"""
from __future__ import annotations

import asyncio
import math
import httpx

from common.logger import info, error
from aio_gateway.isolation import TenantScope, SANDBOX_ROOT


class WorkspaceCleaner:
    """
    Manages workspace lifecycle via AIO shell/exec API.

    - record_access(): touch .last_access on every request
    - cleanup_expired(): find + rm -rf expired workspaces
    """

    def __init__(self, aio_base_url: str, ttl_seconds: int = 7 * 24 * 3600):
        self._aio_url = aio_base_url.rstrip("/")
        self._aio_client = httpx.AsyncClient(timeout=65.0)
        self._ttl_seconds = ttl_seconds
        self._ttl_days = max(1, math.ceil(ttl_seconds / 86400))

    async def record_access(self, scope: TenantScope) -> None:
        """
        Touch .last_access in the tenant's workspace root.
        Fire-and-forget friendly — never raises.
        """
        root = f"{SANDBOX_ROOT}/{scope.user_id}/{scope.session_id}"
        command = f"mkdir -p {root} && touch {root}/.last_access"
        try:
            await self._shell_exec(command, timeout=5)
        except Exception:
            pass  # 静默失败，不影响主请求

    async def cleanup_expired(self) -> int:
        """
        Find and delete workspaces whose .last_access is older than TTL.
        Returns the number of deleted workspace directories.
        """
        command = (
            f"find {SANDBOX_ROOT} -name '.last_access' -mtime +{self._ttl_days} "
            f"-exec dirname {{}} \\; 2>/dev/null | while read d; do "
            f"echo \"deleting: $d\"; rm -rf \"$d\"; done"
        )
        try:
            result = await self._shell_exec(command, timeout=60)
            stdout = result.get("stdout", "")
            deleted_count = stdout.count("deleting:")
            if deleted_count > 0:
                info("清理过期工作域", count=deleted_count, ttl_days=self._ttl_days)
            return deleted_count
        except Exception as e:
            error("工作域清理失败", exc=e)
            return 0

    async def _shell_exec(self, command: str, timeout: int = 30) -> dict:
        url = f"{self._aio_url}/v1/shell/exec"
        body = {"command": command, "timeout": timeout}
        resp = await self._aio_client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}

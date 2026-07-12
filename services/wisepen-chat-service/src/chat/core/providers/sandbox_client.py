from __future__ import annotations

import asyncio
from dataclasses import dataclass
import uuid
from typing import Any

import httpx


class SandboxClientError(Exception):
    def __init__(self, code: str, message: str = "sandbox request failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LeaseContext:
    lease_id: str
    request_id: str
    tenant_id: str
    workspace_id: str
    fencing_token: int
    expires_at: str | None = None


class SandboxClient:
    def __init__(
        self,
        base_url: str,
        from_source: str = "",
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._from_source = from_source
        self._timeout = timeout_seconds
        self._leases: dict[str, LeaseContext] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def read_file(
        self, context: dict[str, Any], path: str, max_chars: int | None = None
    ) -> str:
        data = await self._execute(
            context, "read_file", {"file": path, "max_chars": max_chars}
        )
        return str(data.get("content", "")) if isinstance(data, dict) else str(data)

    async def write_file(
        self, context: dict[str, Any], path: str, content: str
    ) -> dict[str, Any]:
        return await self._execute(context, "write_file", {"file": path, "content": content})

    async def list_directory(
        self, context: dict[str, Any], path: str, recursive: bool = False
    ) -> list[Any]:
        data = await self._execute(
            context, "list_directory", {"path": path, "recursive": recursive}
        )
        return list(data.get("files", [])) if isinstance(data, dict) else []

    async def grep_files(
        self,
        context: dict[str, Any],
        path: str,
        pattern: str,
        recursive: bool = True,
        ignore_case: bool = False,
    ) -> list[Any]:
        data = await self._execute(
            context,
            "grep_files",
            {
                "path": path,
                "pattern": pattern,
                "recursive": recursive,
                "ignore_case": ignore_case,
            },
        )
        return list(data.get("matches", [])) if isinstance(data, dict) else []

    async def replace_in_file(
        self, context: dict[str, Any], path: str, old_str: str, new_str: str
    ) -> dict[str, Any]:
        return await self._execute(
            context,
            "edit_file",
            {"file": path, "old_str": old_str, "new_str": new_str},
        )

    async def shell_exec(
        self,
        context: dict[str, Any],
        command: str,
        exec_dir: str = "/workspace",
        timeout_ms: int = 30000,
    ) -> dict[str, Any]:
        return await self._execute(
            context,
            "shell_exec",
            {"command": command, "exec_dir": exec_dir, "timeout_ms": timeout_ms},
        )

    async def execute_script(
        self, context: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._execute(context, "execute", payload)

    async def allocate_request(self, context: dict[str, Any]) -> LeaseContext:
        request_id = self._request_id(context)
        lease = await self._ensure_lease(context, request_id)
        return lease

    async def release_request(self, request_id: str) -> None:
        lease = self._leases.get(request_id)
        if not lease:
            return
        try:
            await self._request(
                "POST",
                f"/internal/leases/{lease.lease_id}/release",
                {"fencing_token": lease.fencing_token},
            )
        except SandboxClientError as exc:
            if exc.code not in {"LEASE_NOT_FOUND", "LEASE_EXPIRED"}:
                raise
        self._leases.pop(request_id, None)
        self._locks.pop(request_id, None)

    async def _execute(
        self, context: dict[str, Any], operation: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        request_id = self._request_id(context)
        lease = await self._ensure_lease(context, request_id)
        body = {
            "request_id": f"{request_id}_{uuid.uuid4().hex}",
            "tenant_id": lease.tenant_id,
            "workspace_id": lease.workspace_id,
            "fencing_token": lease.fencing_token,
            "operation": operation,
            "payload": payload,
        }
        result = await self._request(
            "POST", f"/internal/leases/{lease.lease_id}/execute", body
        )
        return result.get("data", result) if isinstance(result, dict) else {}

    async def _ensure_lease(
        self, context: dict[str, Any], request_id: str
    ) -> LeaseContext:
        existing = self._leases.get(request_id)
        if existing:
            return existing
        lock = self._locks.setdefault(request_id, asyncio.Lock())
        async with lock:
            existing = self._leases.get(request_id)
            if existing:
                return existing
            tenant_id = str(context.get("user_id") or context.get("tenant_id") or "")
            workspace_id = str(
                context.get("session_id") or context.get("workspace_id") or ""
            )
            result = await self._request(
                "POST",
                "/internal/sandboxes/allocate",
                {
                    "request_id": request_id,
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                },
            )
            lease = LeaseContext(
                lease_id=str(result["lease_id"]),
                request_id=request_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                fencing_token=int(result["fencing_token"]),
                expires_at=result.get("expires_at"),
            )
            self._leases[request_id] = lease
            return lease

    def _request_id(self, context: dict[str, Any]) -> str:
        request_id = str(context.get("request_id") or uuid.uuid4().hex)
        context.setdefault("request_id", request_id)
        return request_id

    async def _request(
        self, method: str, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._from_source:
            headers["X-From-Source"] = self._from_source
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method, f"{self._base_url}{path}", json=body, headers=headers
                )
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise SandboxClientError("SANDBOX_TIMEOUT") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise SandboxClientError("SANDBOX_UNAVAILABLE") from exc
        if not response.is_success:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            code = str(detail or "SANDBOX_UNAVAILABLE")
            raise SandboxClientError(code)
        if not isinstance(payload, dict):
            raise SandboxClientError("SANDBOX_UNAVAILABLE")
        return payload

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx


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
        self._leases: dict[str, str] = {}
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

    async def allocate_request(self, context: dict[str, Any]) -> str:
        """Reserve one sandbox for the whole Chat request before tool execution."""
        request_id = str(context.get("request_id") or uuid.uuid4().hex)
        self._leases[request_id] = await self._ensure_lease(context, request_id)
        return self._leases[request_id]

    async def release_request(self, request_id: str) -> None:
        lease_id = self._leases.pop(request_id, None)
        self._locks.pop(request_id, None)
        if not lease_id:
            return
        await self._request("POST", f"/internal/leases/{lease_id}/release", {})

    async def _execute(
        self, context: dict[str, Any], operation: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        request_id = str(context.get("request_id") or uuid.uuid4().hex)
        lease_id = await self._ensure_lease(context, request_id)
        body = {
            "request_id": f"{request_id}_{uuid.uuid4().hex}",
            "tenant_id": str(context.get("user_id") or context.get("tenant_id") or ""),
            "workspace_id": str(context.get("session_id") or context.get("workspace_id") or ""),
            "operation": operation,
            "payload": payload,
        }
        result = await self._request(
            "POST", f"/internal/leases/{lease_id}/execute", body
        )
        return result.get("data", result) if isinstance(result, dict) else {}

    async def _ensure_lease(self, context: dict[str, Any], request_id: str) -> str:
        if request_id in self._leases:
            return self._leases[request_id]
        lock = self._locks.setdefault(request_id, asyncio.Lock())
        async with lock:
            if request_id not in self._leases:
                result = await self._request(
                    "POST",
                    "/internal/sandboxes/allocate",
                    {
                        "request_id": request_id,
                        "tenant_id": str(context.get("user_id") or context.get("tenant_id") or ""),
                        "workspace_id": str(
                            context.get("session_id") or context.get("workspace_id") or ""
                        ),
                    },
                )
                self._leases[request_id] = str(result["lease_id"])
        return self._leases[request_id]

    async def _request(
        self, method: str, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._from_source:
            headers["X-From-Source"] = self._from_source
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.request(
                method, f"{self._base_url}{path}", json=body, headers=headers
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("sandbox service returned a non-object response")
        return payload

from __future__ import annotations

from dataclasses import dataclass
import json
import uuid
from typing import Any

@dataclass(frozen=True)
class UserSandboxContext:
    user_binding_id: str
    sandbox_id: str
    user_id: str
    user_idle_expires_at: str | None = None
    reuse_count: int = 0


@dataclass(frozen=True)
class TurnLeaseContext:
    lease_id: str
    request_id: str
    tenant_id: str
    workspace_id: str
    fencing_token: int
    expires_at: str | None = None
    sandbox_id: str = ""
    user_binding_id: str = ""
    container_reused: bool = False
    workspace_reused: bool = False


class SandboxClient:
    """Cache user-container hints separately from authoritative turn leases."""

    def __init__(self, *, mcp_client: Any) -> None:
        self._mcp_client = mcp_client
        self._user_sandboxes: dict[str, UserSandboxContext] = {}
        self._turn_leases: dict[str, TurnLeaseContext] = {}
        self._leases = self._turn_leases

    async def allocate_request(self, context: dict[str, Any]) -> TurnLeaseContext:
        request_id = self._request_id(context)
        output = await self._mcp_client.call_tool(
            None,
            "acquire_sandbox",
            {},
            context=context,
        )
        payload = _decode_mcp_payload(output)
        tenant_id = str(payload.get("tenant_id") or context.get("user_id") or "")
        workspace_id = str(payload.get("workspace_id") or context.get("session_id") or "")
        lease = TurnLeaseContext(
            lease_id=str(payload["lease_id"]),
            request_id=str(payload.get("request_id") or request_id),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            fencing_token=int(payload["fencing_token"]),
            expires_at=payload.get("expires_at"),
            sandbox_id=str(payload.get("sandbox_id") or ""),
            user_binding_id=str(payload.get("user_binding_id") or ""),
            container_reused=bool(payload.get("container_reused", False)),
            workspace_reused=bool(payload.get("workspace_reused", False)),
        )
        self._turn_leases[request_id] = lease
        self._user_sandboxes[tenant_id] = UserSandboxContext(
            user_binding_id=lease.user_binding_id,
            sandbox_id=lease.sandbox_id,
            user_id=tenant_id,
            user_idle_expires_at=payload.get("user_idle_expires_at"),
            reuse_count=int(payload.get("reuse_count") or 0),
        )
        return lease

    async def release_request(self, request_id: str) -> None:
        lease = self._turn_leases.get(request_id)
        if not lease:
            return
        await self._mcp_client.call_tool(
            None,
            "release_sandbox",
            {},
            context={
                "request_id": request_id,
                "user_id": lease.tenant_id,
                "session_id": lease.workspace_id,
            },
        )
        self._turn_leases.pop(request_id, None)

    async def delete_workspace(self, user_id: str, session_id: str) -> None:
        await self._mcp_client.call_tool(
            None,
            "delete_sandbox_workspace",
            {},
            context={
                "request_id": f"sandbox-workspace-delete:{uuid.uuid4().hex}",
                "user_id": user_id,
                "session_id": session_id,
            },
        )
        for request_id, lease in list(self._turn_leases.items()):
            if lease.tenant_id == user_id and lease.workspace_id == session_id:
                self._turn_leases.pop(request_id, None)

    async def destroy_session(self, user_id: str, session_id: str) -> None:
        await self.delete_workspace(user_id, session_id)

    def _request_id(self, context: dict[str, Any]) -> str:
        request_id = str(context.get("request_id") or uuid.uuid4().hex)
        context.setdefault("request_id", request_id)
        return request_id

def _decode_mcp_payload(output: str) -> dict[str, Any]:
    payload = json.loads(output)
    if not isinstance(payload, dict):
        raise ValueError("MCP sandbox response must be an object")
    return payload

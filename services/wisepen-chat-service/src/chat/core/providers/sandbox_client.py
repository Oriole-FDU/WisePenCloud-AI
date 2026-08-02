from __future__ import annotations

from dataclasses import dataclass
import json
import uuid
from typing import Any

@dataclass(frozen=True)
class LeaseContext:
    lease_id: str
    request_id: str
    tenant_id: str
    workspace_id: str
    fencing_token: int
    expires_at: str | None = None


class SandboxClient:
    """Manage the MCP sandbox lease for one Chat Turn."""

    def __init__(self, *, mcp_client: Any) -> None:
        self._mcp_client = mcp_client
        self._leases: dict[str, LeaseContext] = {}

    async def allocate_request(self, context: dict[str, Any]) -> LeaseContext:
        request_id = self._request_id(context)
        output = await self._mcp_client.call_tool(
            None,
            "acquire_sandbox",
            {},
            context=context,
        )
        payload = _decode_mcp_payload(output)
        lease = LeaseContext(
            lease_id=str(payload["lease_id"]),
            request_id=str(payload.get("request_id") or request_id),
            tenant_id=str(payload.get("tenant_id") or context.get("user_id") or ""),
            workspace_id=str(payload.get("workspace_id") or context.get("session_id") or ""),
            fencing_token=int(payload["fencing_token"]),
            expires_at=payload.get("expires_at"),
        )
        self._leases[request_id] = lease
        return lease

    async def release_request(self, request_id: str) -> None:
        lease = self._leases.get(request_id)
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
        self._leases.pop(request_id, None)

    def _request_id(self, context: dict[str, Any]) -> str:
        request_id = str(context.get("request_id") or uuid.uuid4().hex)
        context.setdefault("request_id", request_id)
        return request_id

def _decode_mcp_payload(output: str) -> dict[str, Any]:
    payload = json.loads(output)
    if not isinstance(payload, dict):
        raise ValueError("MCP sandbox response must be an object")
    return payload

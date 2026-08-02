from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_sandbox_client_module():
    module_name = "sandbox_client_under_test"
    module_path = (
        Path(__file__).parents[1]
        / "src"
        / "chat"
        / "core"
        / "providers"
        / "sandbox_client.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_sandbox_client = _load_sandbox_client_module()
SandboxClient = _sandbox_client.SandboxClient


class FakeMcpClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def call_tool(self, server, tool_name, arguments, *, context):
        self.calls.append({
            "server": server,
            "tool_name": tool_name,
            "arguments": arguments,
            "context": dict(context),
        })
        if tool_name == "acquire_sandbox":
            return (
                '{"lease_id":"lease-1","request_id":"req-1",'
                '"tenant_id":"user-1","workspace_id":"session-1",'
                '"sandbox_id":"sandbox-1","user_binding_id":"user-binding-1",'
                '"user_idle_expires_at":"2026-08-02T00:30:00Z",'
                '"container_reused":true,"workspace_reused":false,'
                '"fencing_token":7,"expires_at":"2026-08-02T00:00:00Z"}'
            )
        if tool_name == "delete_sandbox_workspace":
            return '{"status":"deleted"}'
        return '{"status":"released"}'


@pytest.mark.asyncio
async def test_sandbox_client_manages_lease_through_mcp():
    mcp = FakeMcpClient()
    client = SandboxClient(mcp_client=mcp)
    context = {"request_id": "req-1", "user_id": "user-1", "session_id": "session-1"}

    lease = await client.allocate_request(context)
    await client.release_request("req-1")

    assert lease.lease_id == "lease-1"
    assert lease.expires_at == "2026-08-02T00:00:00Z"
    assert lease.user_binding_id == "user-binding-1"
    assert lease.sandbox_id == "sandbox-1"
    assert lease.container_reused is True
    assert lease.workspace_reused is False
    assert [call["tool_name"] for call in mcp.calls] == [
        "acquire_sandbox",
        "release_sandbox",
    ]
    assert mcp.calls[0]["arguments"] == {}
    assert mcp.calls[0]["context"] == context
    assert mcp.calls[1]["context"] == context
    assert client._leases == {}
    assert client._user_sandboxes["user-1"].sandbox_id == "sandbox-1"


@pytest.mark.asyncio
async def test_sandbox_client_ignores_release_without_cached_lease():
    mcp = FakeMcpClient()
    client = SandboxClient(mcp_client=mcp)

    await client.release_request("unknown-request")

    assert mcp.calls == []


@pytest.mark.asyncio
async def test_destroy_session_clears_session_and_turn_contexts():
    mcp = FakeMcpClient()
    client = SandboxClient(mcp_client=mcp)
    await client.allocate_request(
        {"request_id": "req-1", "user_id": "user-1", "session_id": "session-1"}
    )

    await client.destroy_session("user-1", "session-1")

    assert mcp.calls[-1]["tool_name"] == "delete_sandbox_workspace"
    assert client._user_sandboxes["user-1"].sandbox_id == "sandbox-1"
    assert client._turn_leases == {}

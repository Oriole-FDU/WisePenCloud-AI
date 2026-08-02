from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from sandbox.transport.mcp import build_sandbox_mcp


class FakeSession:
    async def execute(self, operation: str, payload: dict) -> dict:
        return {"operation": operation, **payload}


def test_sandbox_mcp_registers_expected_tools() -> None:
    server = build_sandbox_mcp(FakeSession())
    names = {tool.name for tool in server._tool_manager.list_tools()}
    assert names == {
        "read_file",
        "write_file",
        "list_directory",
        "grep_files",
        "edit_file",
        "shell_exec",
        "run_sandbox_script",
        "acquire_sandbox",
        "release_sandbox",
    }


def test_sandbox_mcp_documents_logical_workspace_only() -> None:
    server = build_sandbox_mcp(FakeSession())
    tools = {tool.name: tool for tool in server._tool_manager.list_tools()}
    descriptions = "\n".join(str(tool.description or "") for tool in tools.values())

    assert "/workspace" in descriptions
    assert "/home/gem" not in descriptions
    assert "optional" in tools["shell_exec"].description.lower()


@pytest.mark.asyncio
async def test_sandbox_mcp_streamable_http_round_trip() -> None:
    server = build_sandbox_mcp(FakeSession())
    app = FastAPI()
    app.mount("/mcp", server.streamable_http_app())

    async with server.session_manager.run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            async with streamable_http_client("http://test/mcp/", http_client=http) as (
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as client:
                    await client.initialize()
                    result = await client.call_tool("read_file", {"file": "a.txt"})

    assert result.structuredContent["operation"] == "read_file"
    assert result.structuredContent["file"] == "a.txt"

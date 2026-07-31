from contextlib import asynccontextmanager
from types import SimpleNamespace
import sys
import types

import pytest

_settings_module = types.ModuleType("chat.core.config.app_settings")
_settings_module.settings = SimpleNamespace(
    MCP_DEFAULT_TIMEOUT_SECONDS=30.0,
    MCP_MAX_USER_SERVERS=10,
    MCP_MAX_TOOLS_PER_SERVER=50,
    MCP_USER_LIST_TOOLS_CACHE_TTL_SECONDS=60.0,
    MCP_SYSTEM_LIST_TOOLS_CACHE_TTL_SECONDS=60.0,
    TOOL_RESULT_MAX_CHARS=100_000,
)
sys.modules.setdefault("chat.core.config.app_settings", _settings_module)

from chat.application.tools.core.mcp.remote_tool import McpRemoteTool
from chat.service_client.mcp_service_client import McpServiceClient


class _RemoteClient:
    async def call_tool(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        return {"candidates": [{"candidate_id": "[1]"}]}


@pytest.mark.asyncio
async def test_remote_tool_forwards_private_tool_config() -> None:
    client = _RemoteClient()
    tool = McpRemoteTool(
        mcp_client=client,
        server=None,
        remote_name="exa_search",
        definition=SimpleNamespace(
            policy=SimpleNamespace(
                required_context_keys=(),
                timeout_seconds=300.0,
            ),
        ),
        failure_reason="Exa Search Failed",
    )

    result = await tool.execute(
        {},
        config={"api_key": "secret"},
        search_query="wise pen",
        ranking_query="What is WisePen?",
    )

    assert result["candidates"][0]["candidate_id"] == "[1]"
    assert client.kwargs["tool_config"] == {"api_key": "secret"}
    assert client.kwargs["timeout_seconds"] == 300.0
    assert client.args == (
        None,
        "exa_search",
        {
            "search_query": "wise pen",
            "ranking_query": "What is WisePen?",
        },
    )


class _Discovery:
    async def pick(self, service_name, *, strategy):
        return SimpleNamespace(ip="127.0.0.1", port=8080)


class _Session:
    captured_meta = None

    def __init__(self, read_stream, write_stream) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        pass

    async def initialize(self) -> None:
        pass

    async def call_tool(self, name, arguments, *, meta):
        type(self).captured_meta = meta
        return SimpleNamespace(
            structuredContent={"query": "wise pen", "candidates": []},
            isError=False,
        )


@asynccontextmanager
async def _streamable_http_client(**kwargs):
    _streamable_http_client.http_client = kwargs["http_client"]
    yield object(), object(), None


@pytest.mark.asyncio
async def test_service_client_uses_meta_and_preserves_structured_result(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "chat.service_client.mcp_service_client.streamable_http_client",
        _streamable_http_client,
    )
    monkeypatch.setattr(
        "chat.service_client.mcp_service_client.ClientSession",
        _Session,
    )
    client = McpServiceClient(_Discovery(), from_source_secret="source-secret")

    result = await client.call_tool(
        None,
        "exa_search",
        {"search_query": "wise pen", "ranking_query": "What is WisePen?"},
        tool_config={"api_key": "secret"},
        timeout_seconds=300.0,
    )

    assert result == {"query": "wise pen", "candidates": []}
    assert _Session.captured_meta == {
        "wisepen/tool_config": {"api_key": "secret"}
    }
    assert _streamable_http_client.http_client.timeout.read == 300.0
    assert _streamable_http_client.http_client.is_closed

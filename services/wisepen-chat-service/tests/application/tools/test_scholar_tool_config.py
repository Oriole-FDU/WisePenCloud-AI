import json
from types import SimpleNamespace

import pytest

from chat.api.tool_response_converter import build_tool_response
from chat.application.tools.core import ToolDispatcher, ToolInvocation, ToolRegistry
from chat.application.tools.core.mcp.system_mcp_tool_catalog import SystemMcpToolCatalog
from chat.domain.entities.mcp_tool_server_config import McpToolDescriptor


class FakeMcpClient:
    def __init__(self):
        self.calls = []
        self.descriptors = [
            McpToolDescriptor(
                name="search_papers",
                description="Search scholarly papers.",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
            McpToolDescriptor(
                name="lookup_papers",
                description="Look up scholarly papers.",
                input_schema={
                    "type": "object",
                    "properties": {"identifiers": {"type": "array"}},
                    "required": ["identifiers"],
                },
            ),
            McpToolDescriptor(
                name="exa_search",
                description="Search with Exa.",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
        ]

    async def list_tools(self):
        return self.descriptors

    async def call_tool(
        self,
        server,
        tool_name,
        arguments,
        *,
        tool_config=None,
        tool_context=None,
        timeout_seconds=None,
    ):
        self.calls.append((tool_name, arguments, tool_config))
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            structuredContent=None,
            isError=False,
        )


class FakeToolConfigRepository:
    def __init__(self):
        self.configs_by_user = {}

    async def list_tool_configs(self, user_id):
        return self.configs_by_user.get(user_id, [])


def user_tool_config(tool_name, api_key, *, enabled=True):
    return SimpleNamespace(
        tool_name=tool_name,
        enabled=enabled,
        config={},
        secret_config={"api_key": api_key} if api_key is not None else {},
        secret_fingerprints={},
    )


@pytest.fixture
def scholar_tools(monkeypatch):
    from chat.application.tools.core.mcp import system_mcp_tool_catalog

    monkeypatch.setattr(system_mcp_tool_catalog.settings, "MCP_SANDBOX_ENABLED", False)
    mcp_client = FakeMcpClient()
    config_repository = FakeToolConfigRepository()
    catalog = SystemMcpToolCatalog(
        mcp_service_client=mcp_client,
        sandbox_mcp_service_client=SimpleNamespace(),
    )
    registry = ToolRegistry(
        tool_config_repo=config_repository,
        system_mcp_tool_catalog=catalog,
    )
    return registry, config_repository, mcp_client


@pytest.mark.asyncio
async def test_optional_scholar_config_keeps_local_tools_callable(scholar_tools):
    registry, _, mcp_client = scholar_tools
    scope = await registry.derive(user_id="reader")

    assert scope.get("system_search_papers") is not None
    assert scope.get("system_lookup_papers") is not None
    assert scope.get("system_exa_search") is None
    assert "api_key" not in json.dumps(scope.schemas())

    response = build_tool_response(scope.get("system_search_papers"), None)
    assert response.requires_config is False
    assert response.configured is True
    assert response.enabled is True
    assert response.config_schema["properties"]["api_key"]["writeOnly"] is True
    results = await ToolDispatcher().dispatch(
        [
            ToolInvocation("search", "system_search_papers", {"query": "retrieval"}),
            ToolInvocation(
                "lookup",
                "system_lookup_papers",
                {"identifiers": ["arxiv:2601.12345"]},
            ),
        ],
        scope,
    )

    assert all(result.tool_execution_error is None for result in results)
    assert [(name, config) for name, _, config in mcp_client.calls] == [
        ("search_papers", None),
        ("lookup_papers", None),
    ]


@pytest.mark.asyncio
async def test_scholar_api_keys_are_isolated_by_user_and_tool(scholar_tools):
    registry, config_repository, mcp_client = scholar_tools
    config_repository.configs_by_user = {
        "alice": [
            user_tool_config("system_search_papers", "alice-search"),
            user_tool_config("system_lookup_papers", "alice-lookup"),
        ],
        "bob": [
            user_tool_config("system_search_papers", "bob-search"),
            user_tool_config("system_lookup_papers", "bob-lookup"),
        ],
    }

    for user_id in ("alice", "bob"):
        scope = await registry.derive(user_id=user_id)
        results = await ToolDispatcher().dispatch(
            [
                ToolInvocation(
                    "search", "system_search_papers", {"query": "retrieval"}
                ),
                ToolInvocation(
                    "lookup",
                    "system_lookup_papers",
                    {"identifiers": ["arxiv:2601.12345"]},
                ),
            ],
            scope,
        )
        assert all(result.tool_execution_error is None for result in results)
        assert "api_key" not in json.dumps(scope.schemas())

    assert [(name, config) for name, _, config in mcp_client.calls] == [
        ("search_papers", {"api_key": "alice-search"}),
        ("lookup_papers", {"api_key": "alice-lookup"}),
        ("search_papers", {"api_key": "bob-search"}),
        ("lookup_papers", {"api_key": "bob-lookup"}),
    ]


@pytest.mark.asyncio
async def test_suspended_scope_omits_secrets_and_reloads_current_user_config(
    scholar_tools,
):
    registry, config_repository, _ = scholar_tools
    config_repository.configs_by_user["alice"] = [
        user_tool_config("system_search_papers", "before-suspension"),
        user_tool_config("system_lookup_papers", "lookup-key"),
    ]
    scope = await registry.derive(user_id="alice")
    suspended_scope = scope.to_suspension_data()

    assert "configs" not in suspended_scope
    assert "before-suspension" not in json.dumps(suspended_scope)

    config_repository.configs_by_user["alice"] = [
        user_tool_config("system_search_papers", "current-user-key"),
        user_tool_config("system_lookup_papers", "lookup-key", enabled=False),
    ]
    recovered_scope = await registry.recover_derived(suspended_scope, "alice")

    assert recovered_scope.config_for("system_search_papers") == {
        "api_key": "current-user-key"
    }
    assert recovered_scope.get("system_lookup_papers") is None

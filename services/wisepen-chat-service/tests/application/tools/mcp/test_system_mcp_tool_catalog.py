"""验证搜索实验不访问沙箱，以及显式启用后的工具发现行为。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from chat.application.tools.core.mcp import system_mcp_tool_catalog
from chat.domain.entities.mcp_tool_server_config import McpToolDescriptor


@pytest.mark.asyncio
@pytest.mark.parametrize("sandbox_enabled", [False, True])
async def test_search_tools_remain_available_without_sandbox(
    monkeypatch, sandbox_enabled
):
    monkeypatch.setattr(
        system_mcp_tool_catalog.settings, "MCP_SANDBOX_ENABLED", sandbox_enabled
    )
    mcp = SimpleNamespace(
        list_tools=AsyncMock(
            return_value=[
                McpToolDescriptor(
                    name="default_web_search",
                    description="Search the web",
                    input_schema={"type": "object", "properties": {}},
                ),
            ]
        )
    )
    sandbox = SimpleNamespace(
        list_tools=AsyncMock(
            return_value=[
                McpToolDescriptor(
                    name="execute_command",
                    description="Execute a sandbox command",
                    input_schema={"type": "object", "properties": {}},
                ),
            ]
        )
    )
    catalog = system_mcp_tool_catalog.SystemMcpToolCatalog(
        mcp_service_client=mcp,
        sandbox_mcp_service_client=sandbox,
    )

    tools = await catalog.load_system_tools()

    if sandbox_enabled:
        assert set(tools) == {"system_default_web_search", "sandbox_execute_command"}
    else:
        assert set(tools) == {"system_default_web_search"}
        # 没有远端沙箱调用，才能保证实验不会等待沙箱发现的网络超时。
        sandbox.list_tools.assert_not_awaited()

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

from chat.application.tools.core.definition import (
    ToolDefinition,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
)
from chat.application.tools.core.execution.executor import ToolExecutor
from chat.application.tools.core.execution.timeout_budget import timeout_seconds_from_ms
from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.core.registry import ToolScope


def _load_remote_tool_class():
    module_name = "mcp_remote_tool_under_test"
    module_path = (
        Path(__file__).parents[1]
        / "src"
        / "chat"
        / "application"
        / "tools"
        / "core"
        / "mcp"
        / "remote_tool.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.McpRemoteTool


McpRemoteTool = _load_remote_tool_class()


def test_timeout_budget_uses_default_requested_and_grace() -> None:
    kwargs = {"default_timeout_ms": 30000, "max_timeout_ms": 120000}

    assert timeout_seconds_from_ms({}, grace_seconds=10, **kwargs) == 40
    assert timeout_seconds_from_ms(
        {"timeout_ms": 60000}, grace_seconds=10, **kwargs
    ) == 70
    assert timeout_seconds_from_ms(
        {"timeout_ms": 120000}, grace_seconds=5, **kwargs
    ) == 125


@pytest.mark.parametrize("value", [0, 120001, True, "60000"])
def test_timeout_budget_rejects_invalid_values(value: object) -> None:
    with pytest.raises(ValueError, match="timeout_ms"):
        timeout_seconds_from_ms(
            {"timeout_ms": value},
            default_timeout_ms=30000,
            max_timeout_ms=120000,
            grace_seconds=10,
        )


@pytest.mark.asyncio
async def test_tool_executor_uses_argument_aware_timeout() -> None:
    class Tool:
        definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="run_sandbox_script",
                description="test",
                parameters_schema=ToolParametersSchema(
                    {
                        "type": "object",
                        "properties": {"timeout_ms": {"type": "integer"}},
                    }
                ),
            ),
            policy=ToolPolicy(
                timeout_seconds=0.001,
                timeout_seconds_resolver=lambda _: 0.1,
            ),
        )

        async def execute(self, context, config=None, **kwargs):
            await asyncio.sleep(0.01)
            return "ok"

    scope = ToolScope(tools={"run_sandbox_script": Tool()}, context={})
    result = await ToolExecutor(scope).execute_one(
        ToolInvocation("call-1", "run_sandbox_script", {"timeout_ms": 60000})
    )

    assert result.tool_output == "ok"
    assert result.tool_execution_error is None


@pytest.mark.asyncio
async def test_mcp_remote_tool_passes_argument_aware_transport_timeout() -> None:
    class Client:
        def __init__(self) -> None:
            self.timeout_seconds = None

        async def call_tool(
            self, server, tool_name, arguments, context=None, *, timeout_seconds=None
        ):
            self.timeout_seconds = timeout_seconds
            return "ok"

    client = Client()
    definition = ToolDefinition(
        llm_spec=ToolLLMSpec(
            name="run_sandbox_script",
            description="test",
            parameters_schema=ToolParametersSchema(
                {
                    "type": "object",
                    "properties": {"timeout_ms": {"type": "integer"}},
                }
            ),
        ),
        policy=ToolPolicy(
            transport_timeout_seconds_resolver=lambda _: 65.0,
        ),
    )
    tool = McpRemoteTool(
        mcp_client=client,
        server=None,
        remote_name="run_sandbox_script",
        definition=definition,
        failure_reason="failed",
    )

    assert await tool.execute({}, timeout_ms=60000) == "ok"
    assert client.timeout_seconds == 65.0

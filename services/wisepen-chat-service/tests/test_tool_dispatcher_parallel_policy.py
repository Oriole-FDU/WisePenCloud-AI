from __future__ import annotations

import asyncio

import pytest

from chat.application.tools.core import ToolDefinition, ToolLLMSpec, ToolParametersSchema, ToolPolicy
from chat.application.tools.core.execution.dispatcher import ToolDispatcher
from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.core.registry import ToolScope


class TrackingTool:
    def __init__(self, name: str, *, allow_parallel: bool) -> None:
        self.definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name=name,
                description=name,
                parameters_schema=ToolParametersSchema({"type": "object", "properties": {}}),
            ),
            policy=ToolPolicy(allow_parallel=allow_parallel),
        )
        self.active = 0
        self.max_active = 0

    async def execute(self, context, config=None, **kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        return "ok"


@pytest.mark.asyncio
async def test_dispatcher_serializes_tools_that_disallow_parallel_execution() -> None:
    tool = TrackingTool("sandbox", allow_parallel=False)
    scope = ToolScope(tools={"sandbox": tool}, context={})
    invocations = [ToolInvocation(str(index), "sandbox", {}) for index in range(3)]

    result = await ToolDispatcher().dispatch(invocations, scope)

    assert len(result.results) == 3
    assert tool.max_active == 1


@pytest.mark.asyncio
async def test_dispatcher_keeps_explicitly_parallel_tools_parallel() -> None:
    tool = TrackingTool("parallel", allow_parallel=True)
    scope = ToolScope(tools={"parallel": tool}, context={})
    invocations = [ToolInvocation(str(index), "parallel", {}) for index in range(3)]

    await ToolDispatcher().dispatch(invocations, scope)

    assert tool.max_active == 3

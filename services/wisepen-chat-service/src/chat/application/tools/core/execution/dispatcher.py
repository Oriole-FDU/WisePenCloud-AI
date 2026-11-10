from __future__ import annotations

import asyncio

from chat.application.tools.core.definition import ClientToolResult
from chat.application.tools.core.execution.executor import ToolExecutor
from chat.application.tools.core.execution.result import ToolExecutionResult
from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.core.output_cache import ToolOutputCache
from chat.application.tools.core.registry import ToolScope


class ToolDispatcher:
    def __init__(self, *, output_cache: ToolOutputCache) -> None:
        self._output_cache = output_cache

    async def dispatch(
        self,
        invocations: list[ToolInvocation],
        tool_scope: ToolScope,
    ) -> list[ToolExecutionResult]:
        executor = ToolExecutor(tool_scope, output_cache=self._output_cache)
        results = await asyncio.gather(
            *[executor.execute_one(invocation) for invocation in invocations],
            return_exceptions=False,
        )
        return list(results)

    async def client_dispatch(
        self,
        invocations: list[ToolInvocation],
        client_tool_results: list[ClientToolResult],
        tool_scope: ToolScope,
    ) -> list[ToolExecutionResult]:
        executor = ToolExecutor(tool_scope, output_cache=self._output_cache)

        result_map = {
            result.tool_call_id: result
            for result in client_tool_results
        }

        results = await asyncio.gather(
            *[
                executor.execute_client_one(
                    invocation,
                    result_map.get(invocation.tool_call_id),
                )
                for invocation in invocations
            ],
            return_exceptions=False,
        )
        return list(results)

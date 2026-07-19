from __future__ import annotations

import asyncio

from chat.application.tools.core.execution.executor import ToolExecutor
from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.core.output.cache import ToolOutputCache
from chat.application.tools.core.llm.renderer import RenderToolResult
from chat.application.tools.core.registry import ToolScope


class ToolDispatcher:
    __slots__ = ("_output_cache",)

    def __init__(self, *, output_cache: ToolOutputCache) -> None:
        self._output_cache = output_cache

    async def dispatch(
        self,
        invocations: list[ToolInvocation],
        tool_scope: ToolScope,
    ) -> list[RenderToolResult]:
        executor = ToolExecutor(tool_scope, output_cache=self._output_cache)
        return list(
            await asyncio.gather(
                *(executor.execute_one(invocation) for invocation in invocations)
            )
        )

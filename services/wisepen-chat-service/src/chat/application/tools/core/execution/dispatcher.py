from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from chat.application.tools.core.execution.executor import ToolExecutor
from chat.application.tools.core.execution.result import ToolBatchResult
from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.core.registry import ToolScope

if TYPE_CHECKING:
    from chat.application.tools.tool_output_cache import ToolOutputCache
    from chat.application.tools.tool_output_renderer import ToolOutputRenderer


class ToolDispatcher:
    def __init__(
        self,
        *,
        output_renderer: ToolOutputRenderer,
        output_cache: ToolOutputCache,
    ) -> None:
        self._output_renderer = output_renderer
        self._output_cache = output_cache

    async def dispatch(
        self,
        invocations: list[ToolInvocation],
        tool_scope: ToolScope,
    ) -> ToolBatchResult:
        executor = ToolExecutor(
            tool_scope,
            output_renderer=self._output_renderer,
            output_cache=self._output_cache,
        )
        results = await asyncio.gather(
            *[executor.execute_one(invocation) for invocation in invocations],
            return_exceptions=False,
        )
        return ToolBatchResult(results=list(results))

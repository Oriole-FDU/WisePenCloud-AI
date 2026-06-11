import asyncio

from chat.application.tools.core.execution.executor import ToolExecutor
from chat.application.tools.core.execution.result import ToolBatchResult
from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.core.registry import ToolScope


class ToolDispatcher:
    async def dispatch(
        self,
        invocations: list[ToolInvocation],
        tool_scope: ToolScope,
    ) -> ToolBatchResult:
        executor = ToolExecutor(tool_scope)
        results = await asyncio.gather(
            *[executor.execute_one(invocation) for invocation in invocations],
            return_exceptions=False,
        )
        return ToolBatchResult(results=list(results))

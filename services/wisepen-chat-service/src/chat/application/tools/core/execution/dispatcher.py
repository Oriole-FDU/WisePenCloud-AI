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
        results = []
        parallel: list[ToolInvocation] = []

        async def flush_parallel() -> None:
            if not parallel:
                return
            results.extend(
                await asyncio.gather(
                    *[executor.execute_one(item) for item in parallel],
                    return_exceptions=False,
                )
            )
            parallel.clear()

        for invocation in invocations:
            tool = tool_scope.get(invocation.tool_name)
            if tool is not None and tool.definition.policy.allow_parallel:
                parallel.append(invocation)
                continue
            await flush_parallel()
            results.append(await executor.execute_one(invocation))
        await flush_parallel()
        return ToolBatchResult(results=results)

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from chat.application.tools.core.execution.hooks.builtin import (
    JsonSchemaCheck,
    RequiredContextCheck,
)
from chat.application.tools.core.execution.result import (
    ToolExecutionError,
    ToolExecutionResult,
)
from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.core.output.cache import ToolOutputCache
from chat.application.tools.core.llm.renderer import RenderToolResult, render_tool_result
from chat.application.tools.core.output.tool_return import ToolReturn
from chat.application.tools.core.registry import ToolScope


class ToolExecutor:
    def __init__(
        self,
        tool_scope: ToolScope,
        *,
        output_cache: ToolOutputCache,
    ) -> None:
        self._tool_scope = tool_scope
        self._output_cache = output_cache

    async def execute_one(self, invocation: ToolInvocation) -> RenderToolResult:
        result = await self._execute_raw(invocation)
        tool = self._tool_scope.get(invocation.tool_name)
        definition = tool.definition if tool is not None else None

        if result.tool_execution_error is not None:
            error = result.tool_execution_error
            return render_tool_result(
                invocation=invocation,
                output={
                    "error": {
                        "reason": error.reason,
                        "detail_reason": error.detail_reason,
                        "retryable": error.retryable,
                        "metadata": error.metadata,
                    }
                },
                tool_definition=None,
            )

        output = result.tool_output
        if isinstance(output, ToolReturn):
            if definition is None:
                raise RuntimeError("ToolReturn requires a registered tool definition")
            output = await self._output_cache.process(
                tool_return=output,
                invocation=invocation,
                tool_definition=definition,
                session_id=self._tool_scope.context["session_id"],
            )

        return render_tool_result(
            invocation=invocation,
            output=output,
            tool_definition=definition,
        )

    async def _execute_raw(self, invocation: ToolInvocation) -> ToolExecutionResult:
        started_at = datetime.now(timezone.utc)
        tool = self._tool_scope.get(invocation.tool_name)

        try:
            if tool is None:
                raise ToolExecutionError(
                    reason="Tool Unavailable",
                    detail_reason=f"Tool '{invocation.tool_name}' is not available in this scope.",
                    retryable=False,
                )

            tool_config = self._tool_scope.config_for(invocation.tool_name)
            if tool.definition.config_spec is not None and tool_config is None:
                raise ToolExecutionError(
                    reason="Tool Config Missing",
                    detail_reason=f"Tool '{invocation.tool_name}' requires user configuration.",
                    retryable=False,
                )

            preflight_metadata = {}
            for preflight_hook in (
                JsonSchemaCheck(),
                RequiredContextCheck(),
                *tool.definition.preflight_hooks,
            ):
                result = await preflight_hook.check(
                    invocation,
                    tool.definition.policy,
                    tool.definition.llm_spec.parameters_schema,
                    self._tool_scope.context,
                )
                if not result.ok:
                    raise ToolExecutionError(
                        reason="Tool Preflight Failed",
                        detail_reason=result.message,
                        retryable=False,
                    )
                preflight_metadata.update(result.metadata)

            output = await self._run(
                tool.execute(
                    context={
                        **self._tool_scope.context,
                        **preflight_metadata,
                    },
                    config=tool_config,
                    **invocation.tool_call_arguments,
                ),
                timeout_seconds=tool.definition.policy.timeout_seconds,
                tool_name=invocation.tool_name,
            )
            return ToolExecutionResult(
                tool_invocation=invocation,
                tool_output=output,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )
        except ToolExecutionError as exc:
            error = exc
        except Exception as exc:
            error = ToolExecutionError(
                reason="Tool Execution Failed",
                detail_reason=str(exc),
                retryable=False,
            )

        return ToolExecutionResult(
            tool_invocation=invocation,
            tool_output=None,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            tool_execution_error=error,
        )

    async def _run(
        self,
        awaitable: Any,
        timeout_seconds: float | None,
        tool_name: str,
    ) -> Any:
        if timeout_seconds is None:
            return await awaitable
        try:
            return await asyncio.wait_for(awaitable, timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise ToolExecutionError(
                reason="Tool Execution Timeout",
                detail_reason=f"Tool '{tool_name}' timed out.",
                retryable=False,
            ) from exc

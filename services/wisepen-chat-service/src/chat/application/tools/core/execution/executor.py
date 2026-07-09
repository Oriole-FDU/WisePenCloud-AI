from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from chat.application.tools.core.execution.hooks.builtin import (
    JsonSchemaCheck,
    RequiredContextCheck,
)
from chat.application.tools.core.execution.result import ToolExecutionError, ToolExecutionResult
from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.core.llm.renderer import RenderToolResult
from chat.application.tools.core.registry import ToolScope
from common.logger import error

if TYPE_CHECKING:
    from chat.application.tools.tool_output_cache import ToolOutputCache
    from chat.application.tools.tool_output_renderer import ToolOutputRenderer


class ToolExecutor:
    def __init__(
            self,
            tool_scope: ToolScope,
            *,
            output_renderer: ToolOutputRenderer,
            output_cache: ToolOutputCache,
    ) -> None:
        self._tool_scope = tool_scope
        self._output_renderer = output_renderer
        self._output_cache = output_cache

    async def execute_one(self, invocation: ToolInvocation) -> RenderToolResult:
        raw_result = await self._execute_raw(invocation)
        tool = self._tool_scope.get(invocation.tool_name)

        if raw_result.tool_execution_error is not None:
            return self._output_renderer.render_error_result(tool_result=raw_result)

        rendered = self._output_renderer.render_result(tool_result=raw_result)
        return await self._output_cache.process_rendered(
            rendered=rendered,
            tool_definition=tool.definition if tool else None,
            context=self._tool_scope.context,
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

            preflight_hooks = [
                JsonSchemaCheck(),
                RequiredContextCheck(),
                *tool.definition.preflight_hooks,
            ]

            preflight_metadata = {}
            for preflight_hook in preflight_hooks:
                output = await preflight_hook.check(
                    invocation,
                    tool.definition.policy,
                    tool.definition.llm_spec.parameters_schema,
                    self._tool_scope.context,
                )
                if not output.ok:
                    raise ToolExecutionError(
                        reason="Tool Preflight Failed",
                        detail_reason=output.message,
                        retryable=False,
                    )
                preflight_metadata.update(output.metadata)

            output = await self._run(
                tool.execute({
                    **self._tool_scope.context,
                    **preflight_metadata,
                }, **invocation.tool_call_arguments),
                timeout_seconds=tool.definition.policy.timeout_seconds,
                tool_name=invocation.tool_name,
            )

            return ToolExecutionResult(
                tool_invocation=invocation,
                tool_output=output,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                tool_execution_error=None,
            )
        except ToolExecutionError as tool_execution_error:
            return ToolExecutionResult(
                tool_invocation=invocation,
                tool_output=None,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                tool_execution_error=tool_execution_error,
            )
        except Exception as e:
            error(
                "tool execution unexpected error.",
                e=e,
                tool_name=invocation.tool_name,
                tool_call_id=invocation.tool_call_id,
                audit_message="工具发生未捕获异常，已包装为不可重试失败。",
            )
            return ToolExecutionResult(
                tool_invocation=invocation,
                tool_output=None,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                tool_execution_error=ToolExecutionError(
                    reason="Tool Execution Failed",
                    detail_reason=str(e),
                    retryable=False,
                ),
            )

    async def _run(self, awaitable: Any, timeout_seconds: float | None, tool_name: str) -> Any:
        if timeout_seconds is None:
            return await awaitable
        try:
            return await asyncio.wait_for(awaitable, timeout=timeout_seconds)
        except asyncio.TimeoutError as e:
            raise ToolExecutionError(
                reason="Tool Execution Timeout",
                detail_reason=f"Tool '{tool_name}' timed out.",
                retryable=False,
            ) from e

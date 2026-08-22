from __future__ import annotations

import asyncio
import json
from dataclasses import is_dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from pydantic import BaseModel

from chat.application.tools.core.definition import ClientToolResult, ToolRiskLevel
from chat.application.tools.core.execution.hooks.builtin import JsonSchemaCheck, RequiredContextCheck
from chat.application.tools.core.execution.result import (
    ToolOutput,
    ToolExecutionError,
    ToolExecutionResult,
)

from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.core.registry import ToolScope


class ToolExecutor:
    def __init__(self, tool_scope: ToolScope) -> None:
        self._tool_scope = tool_scope

    async def execute_client_one(self, invocation: ToolInvocation, client_tool_result: ClientToolResult) -> ToolExecutionResult:
        started_at = datetime.now(timezone.utc)
        tool = self._tool_scope.get(invocation.tool_name)

        try:
            if tool is None:
                raise ToolExecutionError(
                    reason="Tool Unavailable",
                    detail_reason=f"Tool '{invocation.tool_name}' is not available in this scope.",
                    retryable=False,
                )
            if client_tool_result is None:
                raise ToolExecutionError(
                    reason="Tool Execution Timeout",
                    detail_reason=f"Tool '{invocation.tool_name}' timed out.",
                    retryable=False,
                )
            if client_tool_result.is_error:
                raise ToolExecutionError(
                    reason="Tool Execution Failed",
                    detail_reason=client_tool_result.output,
                    retryable=False,
                )

            output = self._coerce_tool_output(client_tool_result.output)

            return ToolExecutionResult(tool_invocation=invocation, tool_output=output,
                                       started_at=started_at, finished_at=datetime.now(timezone.utc),
                                       tool_execution_error=None)

        except ToolExecutionError as tool_execution_error:
            return ToolExecutionResult(tool_invocation=invocation, tool_output=None,
                                       started_at=started_at, finished_at=datetime.now(timezone.utc),
                                       tool_execution_error=tool_execution_error)


    async def execute_one(self, invocation: ToolInvocation) -> ToolExecutionResult:
        started_at = datetime.now(timezone.utc)
        tool = self._tool_scope.get(invocation.tool_name)

        try:
            if tool is None:
                raise ToolExecutionError(
                    reason="Tool Unavailable",
                    detail_reason=f"Tool '{invocation.tool_name}' is not available in this scope.",
                    retryable=False,
                )

            # 高危工具用户没有批准执行
            if tool.definition.policy is not None and tool.definition.policy.risk_level == ToolRiskLevel.HIGH and invocation.is_approved is False:
                raise ToolExecutionError(
                    reason="Tool Execution Denied",
                    detail_reason=f"Tool '{invocation.tool_name}' was not approved by the user.",
                    retryable=False,
                )

            tool_config = self._tool_scope.config_for(invocation.tool_name)
            if tool.definition.config_spec is not None and tool_config is None:
                raise ToolExecutionError(
                    reason="Tool Config Missing",
                    detail_reason=f"Tool '{invocation.tool_name}' requires user configuration.",
                    retryable=False,
                )

            preflight_hooks = [
                JsonSchemaCheck(),
                RequiredContextCheck(),
                *tool.definition.preflight_hooks,
            ]

            preflight_metadata = {}
            for preflight_hook in preflight_hooks:
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
                else:
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

            output = self._coerce_tool_output(output)

            return ToolExecutionResult(tool_invocation=invocation, tool_output=output,
                                       started_at=started_at, finished_at=datetime.now(timezone.utc),
                                       tool_execution_error=None)
        except ToolExecutionError as tool_execution_error:
            return ToolExecutionResult(tool_invocation=invocation, tool_output=None,
                                       started_at=started_at, finished_at=datetime.now(timezone.utc),
                                       tool_execution_error=tool_execution_error)
        except Exception as exc:
            return ToolExecutionResult(
                tool_invocation=invocation,
                tool_output=None,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                tool_execution_error=ToolExecutionError(
                    reason="Tool Execution Failed",
                    detail_reason=str(exc),
                    retryable=False,
                ),
            )

    async def _run(self, awaitable: Any, timeout_seconds: float | None, tool_name: str) -> Any:
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

    @staticmethod
    def _coerce_tool_output(output: Any) -> ToolOutput:
        if isinstance(output, ToolOutput):
            return output

        if output is None:
            return ToolOutput(content="")

        if isinstance(output, str):
            return ToolOutput(content=output)

        if isinstance(output, bool):
            return ToolOutput(content="true" if output else "false")

        if isinstance(output, int | float):
            return ToolOutput(content=str(output))

        if isinstance(output, BaseModel):
            return ToolOutput(content=output.model_dump_json())

        if is_dataclass(output):
            return ToolOutput(content=json.dumps(asdict(output), ensure_ascii=False))

        if isinstance(output, Mapping):
            value = dict(output)
            return ToolOutput(
                content=json.dumps(value, ensure_ascii=False),
            )

        if isinstance(output, Sequence) and not isinstance(output, str | bytes | bytearray):
            value = list(output)
            return ToolOutput(
                content=json.dumps(value, ensure_ascii=False),
            )

        raise ToolExecutionError(
            reason="Tool Output Invalid",
            detail_reason=f"Tool output is not supported: {type(output).__qualname__}",
            retryable=False,
        )

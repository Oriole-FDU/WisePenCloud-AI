"""服务端工具执行的旁路观测；不调度工具、不更改执行结果、异常或计费。"""

from __future__ import annotations

import asyncio
import json
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from functools import wraps
from typing import Any

from . import observability as turn_obs
from .generation import _Snapshot


_BLOCKED_REASONS = {
    "Tool Unavailable": "unavailable",
    "Tool Execution Denied": "denied",
    "Tool Config Missing": "config_missing",
    "Tool Preflight Failed": "preflight_failed",
}


@dataclass
class ToolObservation:
    span: Any = None
    metadata: dict = field(default_factory=dict)
    tool_name: str = ""
    call_id: str = ""
    result: str = "running"
    error: str | None = None
    output_value: Any = None

    def record_result(self, result):
        if self.span is None:
            return
        try:
            self.metadata.update(
                execution_started_at=result.started_at.isoformat(),
                execution_finished_at=result.finished_at.isoformat(),
                execution_duration_ms=(result.finished_at - result.started_at).total_seconds() * 1000,
            )
            failure = result.tool_execution_error
            if failure is not None:
                fallback = "timeout" if failure.reason == "Tool Execution Timeout" else "error"
                self.result = _BLOCKED_REASONS.get(failure.reason, fallback)
                self.metadata.update(error_reason=failure.reason, retryable=failure.retryable)
                self.error = _Snapshot().copy(str(failure))[:2000]
                payload = {
                    "error": {"reason": failure.reason, "detail_reason": failure.detail_reason, "retryable": failure.retryable},
                }
            else:
                self.result = "success"
                output = result.tool_output
                payload = output.content if output is not None else ""
                # 小型 JSON 文本先解析，便于对其中的 password/api_key 等字段脱敏。
                if isinstance(payload, str) and len(payload) <= turn_obs._max_text_chars:
                    try:
                        parsed = json.loads(payload)
                        if isinstance(parsed, (dict, list)):
                            payload = parsed
                    except (TypeError, ValueError):
                        pass
                images = output.images if output is not None else []
                self.metadata.update(output_image_count=len(images), output_binary_omitted=bool(images))
            self._set_output(payload)
        except Exception as exc:
            turn_obs._report_failure("tool-result", exc)

    def _set_output(self, payload):
        snapshot = _Snapshot()
        # 标准 tool-result 包装保留原调用 ID，避免并发同名工具仅按名字匹配。
        self.output_value = {
            "type": "tool-result", "toolCallId": self.call_id, "toolName": self.tool_name,
            "output": snapshot.copy(payload), "isError": self.result != "success",
        }
        self.metadata["output_truncated"] = snapshot.truncated
        self.metadata["output_binary_omitted"] = self.metadata.get("output_binary_omitted", False) or snapshot.binary_omitted

    def fail(self, exc):
        if self.span is None:
            return
        try:
            self.result = "cancelled" if isinstance(exc, (asyncio.CancelledError, GeneratorExit)) else "error"
            self.error = _Snapshot().copy(str(exc))[:2000] or type(exc).__name__
            self.metadata["error_type"] = type(exc).__name__
            self._set_output({"error": self.error})
        except Exception as telemetry_error:
            turn_obs._report_failure("tool-error", telemetry_error)

    def finish(self):
        if self.span is None:
            return
        try:
            if self.result == "success":
                level = "DEFAULT"
            elif self.result == "cancelled" or self.result in _BLOCKED_REASONS.values():
                level = "WARNING"
            else:
                level = "ERROR"
            self.span.update(
                output=self.output_value, metadata={**self.metadata, "result": self.result},
                level=level, status_message=self.error or self.result,
            )
        except Exception as exc:
            turn_obs._report_failure("tool-update", exc)


@contextmanager
def observe_tool(invocation):
    observation = ToolObservation()
    stack = ExitStack()
    turn = turn_obs._current_turn.get()
    if turn is not None and turn.span is not None and turn_obs._client is not None:
        try:
            observation.tool_name = _Snapshot().copy(invocation.tool_name, key="name")
            observation.call_id = _Snapshot().copy(invocation.tool_call_id, key="id")
            snapshot = _Snapshot()
            parameters = snapshot.copy(invocation.tool_call_arguments)
            observation.metadata = {
                "stage": "tool", "execution_target": "server", "timing_scope": "server_tool_executor",
                "turn_id": turn.metadata["turn_id"], "tool_call_id": observation.call_id,
                "tool_name": observation.tool_name,
                "input_truncated": snapshot.truncated, "input_binary_omitted": snapshot.binary_omitted,
            }
            if invocation.query_loop_iteration is not None:
                observation.metadata["iteration"] = invocation.query_loop_iteration + 1
            observation.span = stack.enter_context(turn_obs._client.start_as_current_observation(
                name=observation.tool_name, as_type="tool", input=parameters,
            ))
            # 只允许本模块创建的服务端工具节点进入 Langfuse，不放开其他自动探针。
            turn_obs.trace.get_current_span().set_attribute("wisepen.observation.kind", "server_tool")
        except Exception as exc:
            observation.span = None
            turn_obs._report_failure("tool-start", exc)
            try:
                stack.close()
            except Exception as close_error:
                turn_obs._report_failure("tool-end", close_error)
            stack = ExitStack()
    try:
        yield observation
    except BaseException as exc:
        observation.fail(exc)
        raise
    finally:
        observation.finish()
        try:
            # 原异常原样向上传递；不给 SDK 自动记录未经脱敏的业务异常。
            stack.close()
        except Exception as exc:
            turn_obs._report_failure("tool-end", exc)


def observe_tool_execution(execute):
    @wraps(execute)
    async def wrapped(executor, invocation, *args, **kwargs):
        turn = turn_obs._current_turn.get()
        if turn is None or turn.span is None or turn_obs._client is None:
            return await execute(executor, invocation, *args, **kwargs)
        with observe_tool(invocation) as observation:
            result = await execute(executor, invocation, *args, **kwargs)
            observation.record_result(result)
            return result

    return wrapped

"""主聊天模型调用的 Generation 埋点；不加载 Nacos，不改变模型请求和计费。

Langfuse 用量及估算成本仅供观测、对账；实际结算始终由 WisePen 原有流程完成。
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import observability as turn_obs


_current_generation: ContextVar[GenerationObservation | None] = ContextVar("langfuse_generation", default=None)
_SECRET_FIELD = re.compile(r"(?i)(api.?key|secret|password|authorization|cookie)")
_DATA_URL = re.compile(r"data:[^;,\s]+;base64,[A-Za-z0-9+/=_-]+")
_STRUCTURAL_FIELDS = {"role", "type", "name", "id", "call_id", "tool_call_id"}


class _Snapshot:
    """复制选定的请求/输出字段；限制总文本、集合大小和深度，省略二进制。"""

    def __init__(self):
        self.remaining = turn_obs._max_text_chars
        self.truncated = False
        self.binary_omitted = False
        self.nodes_remaining = 4000

    def copy(self, value, *, key="", depth=0):
        self.nodes_remaining -= 1
        if self.nodes_remaining < 0:
            self.truncated = True
            return "[item limit]"
        if _SECRET_FIELD.search(key):
            return "[redacted]"
        if depth > 16:
            self.truncated = True
            return "[depth limit]"
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, bytes) or key in {"base64_data", "file_data", "encrypted_content", "signature"}:
            self.binary_omitted = True
            return "[binary or opaque content omitted]"
        if isinstance(value, str):
            if key == "arguments":
                try:
                    arguments = json.loads(value)
                except (TypeError, ValueError):
                    pass
                else:
                    # 工具参数保持有效 JSON，同时对其中的敏感字段和内容做脱敏、限长。
                    return json.dumps(self.copy(arguments, depth=depth + 1), ensure_ascii=False)
            if _DATA_URL.search(value):
                self.binary_omitted = True
                value = _DATA_URL.sub("[inline media omitted]", value)
            value = turn_obs._mask.text(value) if turn_obs._mask else value
            limit = 256 if key in _STRUCTURAL_FIELDS else self.remaining
            self.truncated |= len(value) > limit
            result = value[:limit]
            if key not in _STRUCTURAL_FIELDS:
                self.remaining -= len(result)
            return result
        if isinstance(value, dict):
            self.truncated |= len(value) > 200
            return {
                str(k): self.copy(v, key=str(k), depth=depth + 1)
                for k, v in list(value.items())[:200]
            }
        if isinstance(value, (list, tuple)):
            # 输入过长时优先保留最近消息的文字，保持原有消息顺序和角色。
            messages = bool(value) and all(isinstance(v, dict) and "role" in v for v in value)
            self.truncated |= len(value) > 200
            selected = value[-200:] if messages else value[:200]
            if messages:
                return list(reversed([self.copy(v, depth=depth + 1) for v in reversed(selected)]))
            return [self.copy(v, depth=depth + 1) for v in selected]
        # Gemini 等格式化后的消息可能含 SDK 的 Pydantic 对象；不使用 repr/default=str。
        if hasattr(value, "model_dump"):
            return self.copy(value.model_dump(mode="python", exclude_none=True), key=key, depth=depth + 1)
        self.binary_omitted = True
        return "[unsupported content omitted]"


def _message_projection(messages):
    """格式化失败前仍保留业务层上下文；正常调用会被适配器的实际请求快照替换。"""
    projected = []
    for message in messages:
        role = getattr(message.role, "value", message.role)
        item = {"role": role, "content": message.content or ""}
        if role == "tool":
            item.update(tool_call_id=message.tool_call_id, name=message.tool_name)
        if getattr(message, "reasoning_content", None):
            item["reasoning_content"] = message.reasoning_content
        if getattr(message, "tool_calls", None):
            item["tool_calls"] = [_tool_call(call) for call in message.tool_calls]
        projected.append(item)
    return projected


def _tool_call(call):
    return {
        "id": call.call_id, "type": "function",
        "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
    }


@dataclass
class GenerationObservation:
    span: Any = None
    metadata: dict = field(default_factory=dict)
    input_value: Any = None
    model_parameters: dict = field(default_factory=dict)
    text: str = ""
    reasoning: str = ""
    tool_calls: list = field(default_factory=list)
    completion_start_time: datetime | None = None
    result: str = "running"
    error: str | None = None
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    usage_source: str | None = None

    def set_request(self, value, *, protocol, model_parameters=None):
        if self.span is None:
            return
        try:
            snapshot = _Snapshot()
            self.input_value = snapshot.copy(value)
            self.metadata.update(
                input_protocol=protocol, input_truncated=snapshot.truncated,
                input_binary_omitted=snapshot.binary_omitted,
            )
            if model_parameters:
                safe = _Snapshot().copy(model_parameters)
                self.model_parameters.update({
                    k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                    for k, v in safe.items()
                })
        except Exception as exc:
            turn_obs._report_failure("generation-request", exc)

    def record_event(self, event):
        if self.span is None:
            return
        try:
            kind = getattr(event.type, "value", event.type)
            if kind in {"TEXT_DELTA", "REASONING_DELTA"} and event.delta:
                if self.completion_start_time is None:
                    self.completion_start_time = datetime.now(timezone.utc)
                    self.metadata["first_output_kind"] = kind.lower()
                target = "text" if kind == "TEXT_DELTA" else "reasoning"
                current = getattr(self, target)
                self.metadata["output_truncated"] = self.metadata.get("output_truncated", False) or (
                    len(current) + len(event.delta) > turn_obs._max_text_chars
                )
                setattr(self, target, (current + event.delta)[:turn_obs._max_text_chars])
            elif kind == "TOOL_CALLS":
                self.tool_calls.extend(event.tool_calls or [])
            elif kind == "USAGE" and event.usage is not None:
                usage = event.usage
                self.input_tokens += max(0, int(usage.input_tokens))
                self.output_tokens += max(0, int(usage.output_tokens))
                self.cached_input_tokens += max(0, int(usage.cached_input_tokens))
                source = getattr(usage.usage_source, "value", usage.usage_source)
                self.usage_source = source if self.usage_source in {None, source} else "mixed"
            if getattr(event, "response_id", None):
                self.metadata["response_id"] = str(event.response_id)[:256]
        except Exception as exc:
            turn_obs._report_failure("generation-event", exc)

    def finish(self):
        if self.span is None:
            return
        try:
            message = {"role": "assistant", "content": self.text}
            if self.reasoning:
                message["reasoning_content"] = self.reasoning
            if self.tool_calls:
                # 先脱敏参数字典，再编码为标准 tool_calls.arguments JSON 字符串。
                safe_calls = _Snapshot().copy([
                    {"id": call.call_id, "name": call.name, "arguments": call.arguments}
                    for call in self.tool_calls
                ])
                message["tool_calls"] = [{
                    "id": call["id"], "type": "function", "function": {
                        "name": call["name"], "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                    },
                } for call in safe_calls]
            output = [message] if self.text or self.reasoning or self.tool_calls else []
            snapshot = _Snapshot()
            output = snapshot.copy(output)
            self.metadata.update(
                result=self.result,
                output_truncated=self.metadata.get("output_truncated", False) or snapshot.truncated,
                output_binary_omitted=snapshot.binary_omitted,
                usage_source=self.usage_source or "unavailable",
            )
            usage_details = None
            if self.usage_source is not None:
                total = self.input_tokens + self.output_tokens
                if self.input_tokens == 0 and total > 0:
                    # 与现有业务兜底一致：只有总数时不能把它伪装成准确的输出用量。
                    usage_details = {"total": total}
                    self.metadata["usage_source"] = "provider_total_only"
                else:
                    cached = min(self.cached_input_tokens, self.input_tokens)
                    usage_details = {
                        "input": self.input_tokens - cached,
                        "input_cached_tokens": cached,
                        "output": self.output_tokens, "total": total,
                    }
                self.metadata["input_tokens_total"] = self.input_tokens
                self.metadata["cached_input_tokens"] = self.cached_input_tokens
            if self.error:
                self.metadata["error"] = self.error
            self.span.update(
                input=self.input_value, output=output,
                model_parameters=self.model_parameters, metadata=self.metadata,
                usage_details=usage_details, completion_start_time=self.completion_start_time,
                level="ERROR" if self.result == "error" else "WARNING" if self.result == "cancelled" else "DEFAULT",
                status_message=self.error or self.result,
            )
            if self.result == "error":
                from opentelemetry.trace import Status, StatusCode
                turn_obs.trace.get_current_span().set_status(Status(StatusCode.ERROR, self.error or "model call failed"))
        except Exception as exc:
            turn_obs._report_failure("generation-update", exc)


@contextmanager
def observe_generation(*, model_info, messages, tools, iteration):
    observation = GenerationObservation()
    stack = ExitStack()
    turn = turn_obs._current_turn.get()
    if turn is not None and turn.span is not None and turn_obs._client is not None:
        try:
            if turn_obs._mask:
                turn_obs._mask.remember(model_info.api_key)
            observation.metadata = {
                "stage": "generation", "turn_id": turn.metadata["turn_id"], "iteration": iteration + 1,
                "model_id": str(model_info.model_id), "provider_id": str(model_info.provider_id),
                "provider_type": getattr(model_info.provider_type, "value", model_info.provider_type),
            }
            observation.span = stack.enter_context(turn_obs._client.start_as_current_observation(
                name="llm.generation", as_type="generation", model=model_info.model_name,
            ))
            observation.set_request(
                {"messages": _message_projection(messages), "tools": tools},
                protocol="agent.messages", model_parameters=model_info.runtime_options,
            )
        except Exception as exc:
            observation.span = None
            turn_obs._report_failure("generation-start", exc)
            try:
                stack.close()
            except Exception as close_error:
                turn_obs._report_failure("generation-end", close_error)
            stack = ExitStack()
    token = _current_generation.set(observation)
    try:
        yield observation
    except (asyncio.CancelledError, GeneratorExit):
        observation.result = "cancelled"
        raise
    except BaseException as exc:
        observation.result = "error"
        try:
            observation.error = _Snapshot().copy(str(exc))[:2000]
        except Exception:
            observation.error = type(exc).__name__
        raise
    else:
        observation.result = "success"
    finally:
        observation.finish()
        _current_generation.reset(token)
        try:
            # 业务异常已脱敏；不要再让 SDK 自动写入原始异常。
            stack.close()
        except Exception as exc:
            turn_obs._report_failure("generation-end", exc)


def record_generation_request(value, *, protocol, model_parameters=None):
    if observation := _current_generation.get():
        observation.set_request(value, protocol=protocol, model_parameters=model_parameters)

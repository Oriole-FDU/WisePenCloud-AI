from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from chat.application.tools.core.definition import ToolLLMSpec, ToolDefinition
from chat.application.tools.core.execution.result import ToolExecutionResult
from chat.domain.entities import VisionImage

from pydantic import BaseModel

def schema_renderer(llm_spec: ToolLLMSpec) -> dict[str, Any]:
    """将工具定义渲染为模型可消费的 function calling schema。"""
    return {
        "type": "function",
        "function": {
            "name": llm_spec.name,
            "description": llm_spec.description,
            "parameters": llm_spec.parameters_schema.to_dict(),
        },
    }

@dataclass(frozen=True, slots=True)
class RenderToolResult:
    """可写入模型上下文和会话记录的最终工具输出"""
    tool_call_id: str
    tool_name: str
    persisted_output_placeholder: str | None
    tool_output: str
    images: list[VisionImage]

def tool_result_renderer(tool_result: ToolExecutionResult, tool_definition: ToolDefinition | None) -> RenderToolResult:
    max_output_chars = tool_definition.policy.max_output_chars if tool_definition is not None else None
    if tool_result.tool_execution_error is not None:
        error = tool_result.tool_execution_error
        output = render_tool_output({
            "status": "error",
            "error": {
                "reason": error.reason,
                "detail_reason": error.detail_reason,
                "retryable": error.retryable,
                "metadata": error.metadata,
            },
        })
        images = []
    else:
        tool_output = tool_result.tool_output
        if tool_output is None:
            output = render_tool_output({"status": "success", "output": ""})
            images = []
        else:
            content = tool_output.content
            truncated = False
            if max_output_chars is not None and 0 < max_output_chars < len(content):
                content = content[:max_output_chars] + "\n...[truncated]"
                truncated = True
            output = render_tool_output({
                "status": "success",
                "output": content,
                "truncated": truncated,
            })
            images = tool_output.images

    if tool_definition is None or tool_definition.policy.persist_output:
        persisted_output_placeholder = None
    else:
        try:
            persisted_output_placeholder = tool_definition.policy.persisted_output_placeholder_factory(
                tool_result.tool_invocation.tool_call_arguments,
                output,
            )
        except Exception:
            persisted_output_placeholder = None
        persisted_output_placeholder = persisted_output_placeholder or "[Tool output persisted.]"

    return RenderToolResult(
        tool_call_id=tool_result.tool_invocation.tool_call_id,
        tool_name=tool_result.tool_invocation.tool_name,
        persisted_output_placeholder=persisted_output_placeholder,
        tool_output=output,
        images=images,
    )


def render_tool_output(output: Any) -> str:
    """将工具输出渲染为 ChatMessage.content / SSE 共用的 JSON 字符串"""
    return json.dumps(normalize_json_value(output), ensure_ascii=False)


def normalize_json_value(value: Any) -> Any:
    """把工具返回值收敛到 JSON 兼容值"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")

    if is_dataclass(value):
        return asdict(value)

    if isinstance(value, Mapping):
        return {key: normalize_json_value(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [normalize_json_value(item) for item in value]

    if isinstance(value, (set, frozenset)):
        return [normalize_json_value(item) for item in value]

    raise TypeError(
        f"unsupported tool output type: {type(value).__qualname__}"
    )

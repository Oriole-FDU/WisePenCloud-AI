from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import orjson
from chat.application.tools.core import ToolDefinition
from chat.application.tools.core.definition import ToolLLMSpec
from chat.application.tools.core.llm.invocation import ToolInvocation
from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class RenderToolResult:
    """可写入模型上下文和会话记录的最终工具输出。"""

    tool_call_id: str
    tool_name: str
    persisted_output_placeholder: str | None
    tool_output: str


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


def render_tool_result(
        *,
        invocation: ToolInvocation,
        output: Any,
        tool_definition: ToolDefinition | None,
) -> RenderToolResult:
    """将常见返回值编码为 JSON，不支持的对象降级为原始文本表达。"""
    try:
        tool_output = orjson.dumps(
            output,
            default=_json_default,
            option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_NUMPY,
        ).decode()
    except TypeError:
        tool_output = str(output)

    persisted_output_placeholder: str | None = None
    if tool_definition is not None and not tool_definition.policy.persist_output:
        persisted_output_placeholder = "[Tool output persisted.]"

        try:
            custom_placeholder = (
                tool_definition.policy.persisted_output_placeholder_factory(
                    invocation.tool_call_arguments,
                    tool_output,
                )
            )
            persisted_output_placeholder = (
                    custom_placeholder or persisted_output_placeholder
            )
        except Exception:
            pass

    return RenderToolResult(
        tool_call_id=invocation.tool_call_id,
        tool_name=invocation.tool_name,
        persisted_output_placeholder=persisted_output_placeholder,
        tool_output=tool_output,
    )


def _json_default(value: Any) -> Any:
    """补充 orjson 默认不支持的常见工具返回类型。"""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")

    if isinstance(value, Mapping):
        return dict(value)

    if isinstance(value, (set, frozenset)):
        return list(value)

    raise TypeError(
        f"unsupported tool output type: {type(value).__qualname__}"
    )

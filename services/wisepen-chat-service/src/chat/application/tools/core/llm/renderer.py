from dataclasses import dataclass
from typing import Any

from chat.application.tools.core.definition import ToolLLMSpec, ToolDefinition
from chat.application.tools.core.execution.result import ToolExecutionResult


def schema_renderer(llm_spec: ToolLLMSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": llm_spec.name,
            "description": llm_spec.description,
            "parameters": llm_spec.parameters_schema.to_dict(),
        },
    }

@dataclass(frozen=True)
class RenderToolResult:
    tool_call_id: str
    tool_name: str
    persisted_output_placeholder: str | None
    tool_output: Any | None

def tool_result_renderer(tool_result: ToolExecutionResult, tool_definition: ToolDefinition | None) -> RenderToolResult:
    if tool_result.tool_execution_error is not None:
        error = tool_result.tool_execution_error
        output = f"[Tool Error] {error.reason}"
        if error.detail_reason:
            output = f"{output}: {error.detail_reason}"
    else:
        output = tool_result.tool_output

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
        tool_output=output
    )

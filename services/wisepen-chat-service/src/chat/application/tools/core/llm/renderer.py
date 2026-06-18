from dataclasses import dataclass
from typing import Any

from chat.application.tools.core.definition import ToolLLMSpec


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
    debug_output: Any | None = None

import json
from dataclasses import dataclass
from typing import Any

from common.logger import warn


@dataclass
class ToolCallMessageAccumulator:
    """累积流式 tool call 片段"""
    tool_call_id: str = ""
    tool_name: str = ""
    tool_call_argument_str: str = ""


@dataclass(frozen=True)
class ToolInvocation:
    tool_call_id: str
    tool_name: str
    tool_call_arguments: dict[str, Any]
    query_loop_iteration: int | None = None
    # metadata: dict[str, Any] = field(default_factory=dict)


def tool_call_parse(accumulators: dict[int, ToolCallMessageAccumulator], *, query_loop_iteration: int | None = None) -> list[ToolInvocation]:
    invocations: list[ToolInvocation] = []
    for idx in sorted(accumulators.keys()):
        acc = accumulators[idx]
        try:
            tool_call_arguments = json.loads(acc.tool_call_argument_str) if acc.tool_call_argument_str else {}
        except json.JSONDecodeError as e:
            warn("tool call arguments parse failed.", tool_name=acc.tool_name, exc=e)
            tool_call_arguments = {}
        if not isinstance(tool_call_arguments, dict):
            warn(
                "tool call arguments parse failed beacuse arguments is not a JSON object",
                tool_name=acc.tool_name,
                arguments_parsed_type=type(tool_call_arguments).__name__,
            )
            tool_call_arguments = {}
        invocations.append(
            ToolInvocation(
                tool_call_id=acc.tool_call_id, tool_name=acc.tool_name,
                tool_call_arguments=tool_call_arguments, query_loop_iteration=query_loop_iteration
            )
        )
    return invocations


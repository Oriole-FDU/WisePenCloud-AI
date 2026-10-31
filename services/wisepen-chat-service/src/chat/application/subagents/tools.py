from chat.application.tools.core.definition import (
    ToolDefinition, ToolLLMSpec, ToolParametersSchema, ToolPolicy, ToolSelectionMode,
)
from chat.application.tools.core.execution.result import ToolExecutionError


_PARAMETERS = {
    "subagent_start": ({
        "task": {"type": "string", "minLength": 1},
        "instructions": {"type": "string", "minLength": 1, "maxLength": 32000},
        "agent_id": {"type": "string", "minLength": 1},
        "agent_version": {"type": "integer", "minimum": 1},
        "model": {"type": "string", "minLength": 1},
        "provider_id": {"type": "string", "minLength": 1},
        "context_mode": {"type": "string", "enum": ["fresh", "fork"], "default": "fresh"},
    }, ["task"], "Start a child with dynamic instructions OR a candidate agent_id and fixed agent_version. Returns immediately; wait before answering."),
    "subagent_list": ({}, [], "List child runs in this execution."),
    "subagent_wait": ({
        "run_ids": {"type": "array", "items": {"type": "string", "minLength": 1}, "maxItems": 32},
    }, [], "Wait for selected child runs, or all uncollected runs, and collect their results."),
    "subagent_resume": ({
        "child_id": {"type": "string", "minLength": 1},
        "task": {"type": "string", "minLength": 1},
        "model": {"type": "string", "minLength": 1},
        "provider_id": {"type": "string", "minLength": 1},
    }, ["child_id", "task"], "Continue a saved child in this caller's session, creating a new run with its original role."),
    "subagent_send": ({
        "run_id": {"type": "string", "minLength": 1},
        "input": {"type": "string", "minLength": 1, "maxLength": 32000},
    }, ["run_id", "input"], "Append instructions to an active child at its next safe model iteration boundary."),
    "subagent_cancel": ({
        "run_id": {"type": "string", "minLength": 1},
    }, ["run_id"], "Cancel an active child and its descendants; external side effects are not rolled back."),
}

SUBAGENT_TOOL_NAMES = frozenset(_PARAMETERS)


class SubagentTool:
    def __init__(self, name: str):
        properties, required, description = _PARAMETERS[name]
        self.definition = ToolDefinition(
            llm_spec=ToolLLMSpec(name=name, description=description, parameters_schema=ToolParametersSchema({
                "type": "object", "properties": properties, "required": required, "additionalProperties": False,
            })),
            policy=ToolPolicy(selection_mode=ToolSelectionMode.CONTEXTUAL,
                              required_context_keys=("user_id", "session_id")),
        )

    async def execute(self, context, config=None, **kwargs):
        raise ToolExecutionError(reason="Delegation Unavailable", detail_reason="No active subagent execution binding.", retryable=False)


def subagent_tools():
    return [SubagentTool(name) for name in _PARAMETERS]

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from chat.application.tools.core.definition import ToolParametersSchema, ToolPolicy
from chat.application.tools.core.execution.hooks.base import ToolPreflightResult, ToolPreflightHook
from chat.application.tools.core.llm.invocation import ToolInvocation


class RequiredContextCheck(ToolPreflightHook):
    name = "required_context"


    async def check(self, invocation: ToolInvocation, policy: ToolPolicy,
                    parameters_schema: ToolParametersSchema, context: dict[str, Any]) -> ToolPreflightResult:
        missing = [
            key for key in policy.required_context_keys
            if key not in context
        ]

        if missing:
            return ToolPreflightResult(ok=False, message=f"Missing required context keys for tool '{invocation.tool_name}': {missing}")
        return ToolPreflightResult(ok=True)


class JsonSchemaCheck(ToolPreflightHook):
    name = "json_schema"

    async def check(
        self,
        invocation: ToolInvocation,
        policy: ToolPolicy,
        parameters_schema: ToolParametersSchema,
        context: dict[str, Any],
    ) -> ToolPreflightResult:
        validator = Draft202012Validator(parameters_schema.raw)
        error = best_match(
            validator.iter_errors(invocation.tool_call_arguments)
        )

        if error is None:
            return ToolPreflightResult(ok=True)

        return ToolPreflightResult(
            ok=False,
            message=f"Invalid tool arguments at {error.json_path}: {error.message}",
        )


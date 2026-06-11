from typing import Any

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

    async def check(self, invocation: ToolInvocation, policy: ToolPolicy,
                    parameters_schema: ToolParametersSchema, context: dict[str, Any]) -> ToolPreflightResult:
        arguments = invocation.tool_call_arguments

        for key in parameters_schema.required:
            if key not in arguments:
                return ToolPreflightResult(ok=False, message=f"Missing required tool argument: {key}")

        properties = parameters_schema.properties

        for key, value in arguments.items():
            if key not in properties:
                if parameters_schema.raw.get("additionalProperties", True) is False:
                    return ToolPreflightResult(ok=False, message=f"Unexpected tool argument: {key}")
                continue

            expected_type = properties[key].get("type")
            if expected_type and not self._matches_type(value, expected_type):
                return ToolPreflightResult(
                    ok=False,
                    message=f"Invalid type for argument '{key}'. Expected {expected_type}, got {type(value).__name__}.",
                )

        return ToolPreflightResult(ok=True)

    @staticmethod
    def _matches_type(value: Any, expected_type: str) -> bool:
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "array":
            return isinstance(value, list)
        if expected_type == "null":
            return value is None
        return True

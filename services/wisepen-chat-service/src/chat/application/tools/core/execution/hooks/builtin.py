from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from chat.application.tools.core.definition import ToolParametersSchema, ToolPolicy
from chat.application.tools.core.execution.hooks.base import (
    ToolPreflightHook,
    ToolPreflightResult,
)
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
        arguments = _inject_schema_defaults(
            invocation.tool_call_arguments,
            parameters_schema.raw,
        )
        if isinstance(arguments, dict):
            invocation.tool_call_arguments = arguments

        validator = Draft202012Validator(parameters_schema.raw)
        error = best_match(validator.iter_errors(arguments))

        if error is None:
            return ToolPreflightResult(ok=True)

        return ToolPreflightResult(
            ok=False,
            message=f"Invalid tool arguments at {error.json_path}: {error.message}",
        )


def _inject_schema_defaults(instance: Any, schema: dict[str, Any]) -> Any:
    """在 Tool Call 有默认值的参数缺省值时注入其 JSON Schema 的默认值"""

    if isinstance(instance, dict):
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return instance

        result = dict(instance)
        for property_name, property_schema in properties.items():
            if not isinstance(property_schema, dict):
                continue
            if property_name not in result and "default" in property_schema:
                result[property_name] = deepcopy(property_schema["default"])
            if property_name in result:
                result[property_name] = _inject_schema_defaults(
                    result[property_name],
                    property_schema,
                )
        return result

    if isinstance(instance, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return [
                _inject_schema_defaults(item, item_schema)
                for item in instance
            ]

    return instance


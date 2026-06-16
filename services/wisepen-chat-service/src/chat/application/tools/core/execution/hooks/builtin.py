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
        message = self._validate_value(
            invocation.tool_call_arguments,
            parameters_schema.raw,
            path="arguments",
        )
        if message:
            return ToolPreflightResult(ok=False, message=message)
        return ToolPreflightResult(ok=True)

    def _validate_value(self, value: Any, schema: dict[str, Any], *, path: str) -> str | None:
        expected_type = schema.get("type")
        if expected_type and not self._matches_type(value, expected_type):
            return f"Invalid type for {path}. Expected {expected_type}, got {type(value).__name__}."

        if "enum" in schema and value not in schema["enum"]:
            return f"Invalid value for {path}. Expected one of {schema['enum']}."

        if isinstance(value, str):
            message = self._validate_string(value, schema, path=path)
            if message:
                return message

        if isinstance(value, int | float) and not isinstance(value, bool):
            message = self._validate_number(value, schema, path=path)
            if message:
                return message

        if isinstance(value, list):
            message = self._validate_array(value, schema, path=path)
            if message:
                return message

        if isinstance(value, dict):
            return self._validate_object(value, schema, path=path)

        return None

    def _validate_object(self, value: dict[str, Any], schema: dict[str, Any], *, path: str) -> str | None:
        properties = schema.get("properties") or {}
        for key in schema.get("required") or ():
            if key not in value:
                return f"Missing required tool argument: {self._path(path, key)}"

        for key, item in value.items():
            if key not in properties:
                if schema.get("additionalProperties", True) is False:
                    return f"Unexpected tool argument: {self._path(path, key)}"
                continue

            message = self._validate_value(
                item,
                properties[key],
                path=self._path(path, key),
            )
            if message:
                return message

        return None

    def _validate_array(self, value: list[Any], schema: dict[str, Any], *, path: str) -> str | None:
        min_items = schema.get("minItems")
        if min_items is not None and len(value) < min_items:
            return f"Invalid length for {path}. Expected at least {min_items} items."

        max_items = schema.get("maxItems")
        if max_items is not None and len(value) > max_items:
            return f"Invalid length for {path}. Expected at most {max_items} items."

        item_schema = schema.get("items")
        if not isinstance(item_schema, dict):
            return None

        for index, item in enumerate(value):
            message = self._validate_value(item, item_schema, path=f"{path}[{index}]")
            if message:
                return message

        return None

    @staticmethod
    def _validate_string(value: str, schema: dict[str, Any], *, path: str) -> str | None:
        min_length = schema.get("minLength")
        if min_length is not None and len(value) < min_length:
            return f"Invalid length for {path}. Expected at least {min_length} characters."

        max_length = schema.get("maxLength")
        if max_length is not None and len(value) > max_length:
            return f"Invalid length for {path}. Expected at most {max_length} characters."

        return None

    @staticmethod
    def _validate_number(value: int | float, schema: dict[str, Any], *, path: str) -> str | None:
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            return f"Invalid value for {path}. Expected at least {minimum}."

        maximum = schema.get("maximum")
        if maximum is not None and value > maximum:
            return f"Invalid value for {path}. Expected at most {maximum}."

        return None

    @staticmethod
    def _matches_type(value: Any, expected_type: str | list[str]) -> bool:
        if isinstance(expected_type, list):
            return any(JsonSchemaCheck._matches_type(value, item) for item in expected_type)

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

    @staticmethod
    def _path(parent: str, key: str) -> str:
        return f"{parent}.{key}" if parent else key

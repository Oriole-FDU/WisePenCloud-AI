from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from chat.application.tools.core.definition import ToolParametersSchema, ToolPolicy
from chat.application.tools.core.execution.hooks.base import ToolPreflightHook, ToolPreflightResult
from chat.application.tools.core.llm.invocation import ToolInvocation


class RequiredContextCheck(ToolPreflightHook):
    """校验工具执行所需的 context key 是否齐备。"""

    name = "required_context"

    async def check(
        self,
        invocation: ToolInvocation,
        policy: ToolPolicy,
        parameters_schema: ToolParametersSchema,
        context: dict[str, Any],
    ) -> ToolPreflightResult:
        if missing := [key for key in policy.required_context_keys if key not in context]:
            return ToolPreflightResult(
                ok=False,
                message=f"Missing required context keys for tool '{invocation.tool_name}': {missing}",
            )
        return ToolPreflightResult(ok=True)


class JsonSchemaCheck(ToolPreflightHook):
    """校验工具调用参数是否符合 JSON Schema。

    委托给 jsonschema 库，而非手写递归校验器。
    OpenAI function-calling 协议禁用 oneOf/anyOf/allOf/$ref，schema 永远是单一线性路径，
    所以不需要 best_match 那套多分支裁决逻辑——iter_errors 的第一条就是唯一会出现的错误。
    """

    name = "json_schema"

    async def check(
        self,
        invocation: ToolInvocation,
        policy: ToolPolicy,
        parameters_schema: ToolParametersSchema,
        context: dict[str, Any],
    ) -> ToolPreflightResult:
        validator = Draft202012Validator(parameters_schema.raw)

        error = next(iter(validator.iter_errors(invocation.tool_call_arguments)), None)
        if error is None:
            return ToolPreflightResult(ok=True)

        return ToolPreflightResult(ok=False, message=_format_error(error, invocation.tool_name))


def _format_error(error: ValidationError, tool_name: str) -> str:
    """将 jsonschema 的 ValidationError 转为人类可读提示，仅拼接路径前缀。"""
    # absolute_path 是 deque，形如 deque(['a', 'b', 0])；点号拼接成 JSON Pointer 风格路径
    path = ".".join(str(part) for part in error.absolute_path) or "arguments"
    return f"Invalid arguments for '{tool_name}' at {path}: {error.message}"
from __future__ import annotations

from typing import Any, Dict

from chat.application.tools.core.definition import ToolDefinition, ToolLLMSpec, ToolParametersSchema, ToolPolicy
from chat.core.config.app_settings import settings
from chat.core.providers.sandbox_client import SandboxClient


class RunSandboxScriptTool:
    def __init__(self, sandbox: SandboxClient) -> None:
        self._sandbox = sandbox

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="run_sandbox_script",
                description=(
                    "Run a script package in the sandbox environment. "
                    "Provide package_id and optional entry/args/env/timeout_ms/limits."
                ),
                parameters_schema=ToolParametersSchema(
                    {
                        "type": "object",
                        "properties": {
                            "package_id": {"type": "string", "minLength": 1},
                            "entry": {"type": "string"},
                            "args": {"type": "array", "items": {"type": "string"}},
                            "env": {"type": "object", "additionalProperties": {"type": "string"}},
                            "timeout_ms": {"type": "integer", "minimum": 1},
                            "limits": {"type": "object"},
                        },
                        "required": ["package_id"],
                    }
                ),
            ),
            policy=ToolPolicy(expose_by_default=True),
        )

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        package_id = str(kwargs.get("package_id") or "").strip()
        if not package_id:
            return "[Tool Error] missing package_id"
        payload = {
            "package_id": package_id,
            "entry": kwargs.get("entry"),
            "args": kwargs.get("args") if isinstance(kwargs.get("args"), list) else [],
            "env": kwargs.get("env") if isinstance(kwargs.get("env"), dict) else {},
            "timeout_ms": kwargs.get("timeout_ms"),
            "limits": kwargs.get("limits") if isinstance(kwargs.get("limits"), dict) else {},
        }
        try:
            result = await self._sandbox.execute_script(context, payload)
        except Exception as exc:
            return f"[Tool Error] sandbox request failed: {type(exc).__name__}: {exc}"
        return self._truncate(self._format_result(result))

    def _format_result(self, result: Dict[str, Any]) -> str:
        lines = [
            "[Sandbox Execution]",
            f"status: {result.get('status')}",
            f"request_id: {result.get('request_id')}",
            f"sandbox_id: {result.get('sandbox_id')}",
            f"exit_code: {result.get('exit_code')}",
            f"duration_ms: {result.get('duration_ms')}",
            "stdout:",
            str(result.get("stdout") or result.get("content") or ""),
            "stderr:",
            str(result.get("stderr") or ""),
        ]
        return "\n".join(lines)

    def _truncate(self, text: str) -> str:
        limit = settings.TOOL_RESULT_MAX_CHARS
        return text[:limit] + "\n...[truncated]..." if limit and len(text) > limit else text

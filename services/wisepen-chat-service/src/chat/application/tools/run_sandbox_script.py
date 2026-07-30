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
                    "Run source code in the sandbox environment. "
                    "Provide language and code, with optional timeout_ms and limits."
                ),
                parameters_schema=ToolParametersSchema(
                    {
                        "type": "object",
                        "properties": {
                            "language": {"type": "string", "minLength": 1},
                            "code": {"type": "string", "minLength": 1},
                            "timeout_ms": {"type": "integer", "minimum": 1},
                            "limits": {"type": "object"},
                        },
                        "required": ["language", "code"],
                    }
                ),
            ),
            policy=ToolPolicy(expose_by_default=True),
        )

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        language = str(kwargs.get("language") or "").strip()
        code = str(kwargs.get("code") or "")
        if not language or not code:
            return "[Tool Error] missing language or code"
        payload = {
            "language": language,
            "code": code,
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

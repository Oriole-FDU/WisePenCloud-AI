from typing import Any, Dict
from chat.core.config.app_settings import settings
from chat.core.providers.sandbox.base import FileSystemProvider
from chat.application.tools.core.definition import (
    ToolDefinition, ToolLLMSpec, ToolParametersSchema, ToolPolicy,
    ToolRiskLevel,
)


class ShellExecTool:
    def __init__(self, fs_provider: FileSystemProvider) -> None:
        self._fs = fs_provider

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="shell_exec",
                description="Execute a shell command in the sandbox. Returns stdout, stderr, and exit code. Commands timeout after 30 seconds.",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command to execute"},
                        "exec_dir": {"type": "string", "description": "Working directory under /workspace/ (default: /workspace/)"},
                    },
                    "required": ["command"],
                }),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                risk_level=ToolRiskLevel.MEDIUM,
                max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
                timeout_seconds=35,
            ),
        )

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        cmd = str(kwargs.get("command") or "").strip()
        if not cmd: return "[Tool Error] missing 'command'"
        exec_dir = str(kwargs.get("exec_dir") or "/workspace").strip()
        uid = str(context.get("user_id") or "")
        sid = str(context.get("session_id") or "")
        try:
            r = await self._fs.shell_exec(cmd, exec_dir=exec_dir, user_id=uid, session_id=sid)
        except Exception as e:
            return f"[Tool Error] shell_exec failed: {type(e).__name__}: {e}"
        if not isinstance(r, dict):
            return str(r)
        ec = r.get("exit_code", "?")
        out = r.get("stdout", "") or ""
        err = r.get("stderr", "") or ""
        parts = [f"[Shell] exit_code={ec}"]
        limit = settings.TOOL_RESULT_MAX_CHARS
        if out:
            parts.append(f"stdout:\n{_trunc(out, limit)}")
        if err:
            parts.append(f"stderr:\n{_trunc(err, limit)}")
        return "\n".join(parts)


def _trunc(t: str, limit: int) -> str:
    if limit and len(t) > limit:
        return t[:limit] + "\n...[truncated]..."
    return t

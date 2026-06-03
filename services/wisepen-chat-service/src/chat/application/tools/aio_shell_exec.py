from typing import Any, Dict

from chat.core.config.app_settings import settings
from chat.core.providers.sandbox.aio_gateway_provider import AioGatewayProvider
from chat.domain.interfaces.tool import BaseTool


class ShellExecTool(BaseTool):
    """Execute a shell command in the AIO sandbox."""

    def __init__(self, aio_gateway: AioGatewayProvider) -> None:
        self._gateway = aio_gateway

    @property
    def name(self) -> str:
        return "shell_exec"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command in your sandbox. "
            "Commands run from your workspace directory (/workspace/) by default. "
            "Returns stdout, stderr, and exit code. "
            "Use this to run scripts, install packages, compile code, or check system state. "
            "The command has a timeout of 30 seconds."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute (e.g. 'python main.py' or 'ls -la')",
                },
                "exec_dir": {
                    "type": "string",
                    "description": "Working directory for the command, under /workspace/. Defaults to /workspace/.",
                },
            },
            "required": ["command"],
        }

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        command = str(kwargs.get("command") or "").strip()
        exec_dir = str(kwargs.get("exec_dir") or "/workspace").strip()
        user_id = str(context.get("user_id") or "")
        session_id = str(context.get("session_id") or "")

        if not command:
            return "[Tool Error] missing 'command' parameter"

        try:
            result = await self._gateway.shell_exec(
                command, exec_dir=exec_dir,
                user_id=user_id, session_id=session_id,
            )
        except Exception as e:
            return f"[Tool Error] shell_exec failed: {type(e).__name__}: {e}"

        if not isinstance(result, dict):
            return f"[Shell Result]\n{result}"

        exit_code = result.get("exit_code", "?")
        stdout = result.get("stdout", "") or ""
        stderr = result.get("stderr", "") or ""

        parts = [f"[Shell Result] exit_code={exit_code}"]
        if stdout:
            parts.append(f"stdout:\n{_truncate(stdout)}")
        if stderr:
            parts.append(f"stderr:\n{_truncate(stderr)}")
        return "\n".join(parts)


def _truncate(text: str) -> str:
    limit = settings.TOOL_RESULT_MAX_CHARS
    if limit and len(text) > limit:
        return text[:limit] + "\n...[truncated]..."
    return text

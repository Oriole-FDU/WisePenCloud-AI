from typing import Any, Dict

from chat.core.config.app_settings import settings
from chat.core.providers.sandbox_client import SandboxClient
from chat.application.tools.core.definition import (
    Tool, ToolDefinition, ToolLLMSpec, ToolParametersSchema, ToolPolicy,
)


class ReadFileTool:
    """Read a file from the AIO sandbox workspace."""

    def __init__(self, sandbox: SandboxClient) -> None:
        self._sandbox = sandbox

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="read_file",
                description=(
                    "Read the contents of a file from your sandbox workspace. "
                    "Your workspace root is /workspace/. All paths must be under /workspace/ "
                    "or use relative paths (e.g. /workspace/main.py or outputs/log.txt)."
                ),
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "description": "File path under /workspace/"},
                        "max_chars": {"type": "integer", "description": f"Max characters (default: {settings.TOOL_RESULT_MAX_CHARS})"},
                    },
                    "required": ["file"],
                }),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
            ),
        )

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        file_path = str(kwargs.get("file") or "").strip()
        if not file_path:
            return "[Tool Error] missing 'file'"

        max_chars = kwargs.get("max_chars") or settings.TOOL_RESULT_MAX_CHARS
        try:
            content = await self._sandbox.read_file(context, file_path, max_chars=max_chars)
        except Exception as e:
            return f"[Tool Error] read_file failed: {type(e).__name__}: {e}"

        if not content:
            return "[File is empty]"

        limit = settings.TOOL_RESULT_MAX_CHARS
        if limit and len(content) > limit:
            content = content[:limit] + "\n...[truncated]..."
        return content

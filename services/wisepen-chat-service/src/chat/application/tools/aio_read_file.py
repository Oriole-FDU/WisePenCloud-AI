from typing import Any, Dict

from chat.core.config.app_settings import settings
from chat.core.providers.sandbox.aio_gateway_provider import AioGatewayProvider
from chat.domain.interfaces.tool import BaseTool


class ReadFileTool(BaseTool):
    """Read a file from the AIO sandbox workspace."""

    def __init__(self, aio_gateway: AioGatewayProvider) -> None:
        self._gateway = aio_gateway

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file from your sandbox workspace. "
            "Use this to inspect files before editing or executing them. "
            "Your workspace root is /workspace/. All paths must be under /workspace/ "
            "or use relative paths (e.g. /workspace/main.py or outputs/log.txt)."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "File path under /workspace/ (e.g. /workspace/main.py) or relative path",
                },
                "max_chars": {
                    "type": "integer",
                    "description": f"Maximum characters to read (default: {settings.TOOL_RESULT_MAX_CHARS})",
                },
            },
            "required": ["file"],
        }

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        file_path = str(kwargs.get("file") or "").strip()
        if not file_path:
            return "[Tool Error] missing 'file' parameter"

        max_chars = kwargs.get("max_chars") or settings.TOOL_RESULT_MAX_CHARS
        user_id = str(context.get("user_id") or "")
        session_id = str(context.get("session_id") or "")

        try:
            content = await self._gateway.read_file(
                file_path, max_chars=max_chars,
                user_id=user_id, session_id=session_id,
            )
        except Exception as e:
            return f"[Tool Error] read_file failed: {type(e).__name__}: {e}"

        if not content:
            return "[File is empty]"

        limit = settings.TOOL_RESULT_MAX_CHARS
        if limit and len(content) > limit:
            content = content[:limit] + "\n...[truncated]..."
        return content

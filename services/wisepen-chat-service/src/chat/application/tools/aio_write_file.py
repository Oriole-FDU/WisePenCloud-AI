from typing import Any, Dict

from chat.core.providers.sandbox.aio_gateway_provider import AioGatewayProvider
from chat.domain.interfaces.tool import BaseTool


class WriteFileTool(BaseTool):
    """Write content to a file in the AIO sandbox workspace."""

    def __init__(self, aio_gateway: AioGatewayProvider) -> None:
        self._gateway = aio_gateway

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Write content to a file in your sandbox workspace. "
            "Creates the file if it doesn't exist, or overwrites it if it does. "
            "Your workspace root is /workspace/. All paths must be under /workspace/ "
            "or use relative paths (e.g. /workspace/main.py or scripts/analyze.py)."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Path where to write the file, under /workspace/ (e.g. /workspace/main.py)",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file",
                },
            },
            "required": ["file", "content"],
        }

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        file_path = str(kwargs.get("file") or "").strip()
        content = str(kwargs.get("content") or "")

        if not file_path:
            return "[Tool Error] missing 'file' parameter"
        if not content:
            return "[Tool Error] missing 'content' parameter"

        user_id = str(context.get("user_id") or "")
        session_id = str(context.get("session_id") or "")

        try:
            result = await self._gateway.write_file(
                file_path, content,
                user_id=user_id, session_id=session_id,
            )
        except Exception as e:
            return f"[Tool Error] write_file failed: {type(e).__name__}: {e}"

        bytes_written = result.get("bytes_written", len(content.encode("utf-8")))
        return f"Successfully wrote {bytes_written} bytes to {file_path}"

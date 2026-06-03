from typing import Any, Dict

from chat.core.providers.sandbox.aio_gateway_provider import AioGatewayProvider
from chat.domain.interfaces.tool import BaseTool


class EditFileTool(BaseTool):
    """
    Perform exact string replacement in a file (Aider-style editing).
    Uses str_replace_editor pattern: find the exact old_str and replace with new_str.
    """

    def __init__(self, aio_gateway: AioGatewayProvider) -> None:
        self._gateway = aio_gateway

    @property
    def name(self) -> str:
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Perform an exact string replacement in a file in your sandbox workspace. "
            "The old_str must match exactly (including whitespace and indentation) "
            "and appear only once in the file. "
            "Use this to make precise edits to files. "
            "Important: copy the exact text you want to replace from the file content "
            "(use read_file first to get the exact text). "
            "Your workspace root is /workspace/."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Path to the file to edit, under /workspace/",
                },
                "old_str": {
                    "type": "string",
                    "description": "The exact text to replace. Must match exactly one occurrence in the file.",
                },
                "new_str": {
                    "type": "string",
                    "description": "The text to replace it with.",
                },
            },
            "required": ["file", "old_str", "new_str"],
        }

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        file_path = str(kwargs.get("file") or "").strip()
        old_str = str(kwargs.get("old_str") or "")
        new_str = str(kwargs.get("new_str") or "")
        user_id = str(context.get("user_id") or "")
        session_id = str(context.get("session_id") or "")

        if not file_path:
            return "[Tool Error] missing 'file' parameter"
        if not old_str:
            return "[Tool Error] missing 'old_str' parameter"

        try:
            result = await self._gateway.replace_in_file(
                file_path, old_str, new_str,
                user_id=user_id, session_id=session_id,
            )
        except Exception as e:
            return f"[Tool Error] edit_file failed: {type(e).__name__}: {e}"

        bytes_written = result.get("bytes_written", 0) if isinstance(result, dict) else 0
        return f"Successfully edited {file_path} ({bytes_written} bytes written after replacement)"

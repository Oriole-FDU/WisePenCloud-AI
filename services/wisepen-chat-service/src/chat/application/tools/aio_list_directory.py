from typing import Any, Dict

from chat.core.providers.sandbox.aio_gateway_provider import AioGatewayProvider
from chat.domain.interfaces.tool import BaseTool


class ListDirectoryTool(BaseTool):
    """List files and directories in the AIO sandbox workspace."""

    def __init__(self, aio_gateway: AioGatewayProvider) -> None:
        self._gateway = aio_gateway

    @property
    def name(self) -> str:
        return "list_directory"

    @property
    def description(self) -> str:
        return (
            "List files and directories in your sandbox workspace. "
            "Use this to explore the workspace structure, find files, "
            "and verify that files you wrote exist. "
            "Your workspace root is /workspace/."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path under /workspace/ to list. Defaults to /workspace/ if omitted.",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Whether to list subdirectories recursively (default: false)",
                },
            },
            "required": ["path"],
        }

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        path = str(kwargs.get("path") or "/workspace").strip()
        recursive = bool(kwargs.get("recursive"))
        user_id = str(context.get("user_id") or "")
        session_id = str(context.get("session_id") or "")

        try:
            files = await self._gateway.list_directory(
                path, recursive=recursive,
                user_id=user_id, session_id=session_id,
            )
        except Exception as e:
            return f"[Tool Error] list_directory failed: {type(e).__name__}: {e}"

        if not files:
            return f"Directory '{path}' is empty or does not exist."

        lines = [f"Contents of {path} ({len(files)} items):"]
        for f in files:
            if isinstance(f, dict):
                name = f.get("name", "?")
                size = f.get("size", 0)
                is_dir = f.get("is_directory", False)
                prefix = "📁" if is_dir else "📄"
                lines.append(f"  {prefix} {name} ({size} bytes)")
            else:
                lines.append(f"  - {f}")
        return "\n".join(lines)

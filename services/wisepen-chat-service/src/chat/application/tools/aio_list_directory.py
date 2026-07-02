from typing import Any, Dict
from chat.core.providers.sandbox.base import FileSystemProvider
from chat.application.tools.core.definition import (
    ToolDefinition, ToolLLMSpec, ToolParametersSchema, ToolPolicy,
)


class ListDirectoryTool:
    def __init__(self, fs_provider: FileSystemProvider) -> None:
        self._fs = fs_provider

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="list_directory",
                description="List files and directories under /workspace/.",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path under /workspace/"},
                        "recursive": {"type": "boolean", "description": "Recurse subdirectories (default: false)"},
                    },
                    "required": ["path"],
                }),
            ),
            policy=ToolPolicy(expose_by_default=True),
        )

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        path = str(kwargs.get("path") or "/workspace").strip()
        recursive = bool(kwargs.get("recursive"))
        uid = str(context.get("user_id") or "")
        sid = str(context.get("session_id") or "")
        try:
            files = await self._fs.list_directory(path, recursive=recursive, user_id=uid, session_id=sid)
        except Exception as e:
            return f"[Tool Error] list_directory failed: {type(e).__name__}: {e}"
        if not files:
            return f"Directory '{path}' is empty or does not exist."
        lines = [f"Contents of {path} ({len(files)} items):"]
        for f in files:
            if isinstance(f, dict):
                lines.append(f"  {'[D]' if f.get('is_directory') else '[F]'} {f.get('name','?')} ({f.get('size',0)} bytes)")
            else:
                lines.append(f"  - {f}")
        return "\n".join(lines)

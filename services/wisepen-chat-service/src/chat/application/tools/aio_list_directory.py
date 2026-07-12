from typing import Any, Dict
from chat.core.providers.sandbox_client import SandboxClient
from chat.application.tools.core.definition import (
    ToolDefinition, ToolLLMSpec, ToolParametersSchema, ToolPolicy,
)


class ListDirectoryTool:
    def __init__(self, sandbox: SandboxClient) -> None:
        self._sandbox = sandbox

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
        try:
            files = await self._sandbox.list_directory(context, path, recursive=recursive)
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

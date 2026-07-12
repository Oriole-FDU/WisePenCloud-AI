from typing import Any, Dict
from chat.core.config.app_settings import settings
from chat.core.providers.sandbox_client import SandboxClient
from chat.application.tools.core.definition import (
    ToolDefinition, ToolLLMSpec, ToolParametersSchema, ToolPolicy,
)


class GrepFilesTool:
    def __init__(self, sandbox: SandboxClient) -> None:
        self._sandbox = sandbox

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="grep_files",
                description="Search for a regex pattern in files under /workspace/. Returns matching lines with file and line number.",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path under /workspace/"},
                        "pattern": {"type": "string", "description": "Regex pattern"},
                        "recursive": {"type": "boolean", "description": "Search subdirectories (default: true)"},
                        "ignore_case": {"type": "boolean", "description": "Case-insensitive (default: false)"},
                    },
                    "required": ["path", "pattern"],
                }),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                max_output_chars=settings.TOOL_RESULT_MAX_CHARS,
            ),
        )

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        path = str(kwargs.get("path") or "").strip()
        pattern = str(kwargs.get("pattern") or "").strip()
        if not path: return "[Tool Error] missing 'path'"
        if not pattern: return "[Tool Error] missing 'pattern'"
        recursive = kwargs.get("recursive", True)
        ignore_case = kwargs.get("ignore_case", False)
        try:
            matches = await self._sandbox.grep_files(
                context,
                path, pattern,
                recursive=recursive if recursive is not None else True,
                ignore_case=ignore_case if ignore_case is not None else False,
            )
        except Exception as e:
            return f"[Tool Error] grep_files failed: {type(e).__name__}: {e}"
        if not matches:
            return f"No matches for '{pattern}' in {path}"
        lines = [f"Found {len(matches)} match(es):"]
        for m in matches[:50]:
            fname = m.get("file", "?") if isinstance(m, dict) else "?"
            lnum = m.get("line_number", 0) if isinstance(m, dict) else 0
            content = m.get("line", "") if isinstance(m, dict) else str(m)
            lines.append(f"  {fname}:{lnum}: {content}")
        if len(matches) > 50:
            lines.append(f"  ... ({len(matches) - 50} more)")
        text = "\n".join(lines)
        limit = settings.TOOL_RESULT_MAX_CHARS
        if limit and len(text) > limit:
            text = text[:limit] + "\n...[truncated]..."
        return text

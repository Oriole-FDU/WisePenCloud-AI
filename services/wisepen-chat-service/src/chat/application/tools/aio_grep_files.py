from typing import Any, Dict

from chat.core.config.app_settings import settings
from chat.core.providers.sandbox.aio_gateway_provider import AioGatewayProvider
from chat.domain.interfaces.tool import BaseTool


class GrepFilesTool(BaseTool):
    """Search for a pattern in files within the sandbox workspace."""

    def __init__(self, aio_gateway: AioGatewayProvider) -> None:
        self._gateway = aio_gateway

    @property
    def name(self) -> str:
        return "grep_files"

    @property
    def description(self) -> str:
        return (
            "Search for a regex pattern in files under a directory in your sandbox workspace. "
            "Returns matching lines with file name and line number. "
            "Use this to find specific code patterns, function definitions, or text in your workspace."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path under /workspace/ to search in",
                },
                "pattern": {
                    "type": "string",
                    "description": "Regular expression pattern to search for",
                },
                "recursive": {
                    "type": "boolean",
                    "description": "Search recursively in subdirectories (default: true)",
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive search (default: false)",
                },
            },
            "required": ["path", "pattern"],
        }

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        path = str(kwargs.get("path") or "").strip()
        pattern = str(kwargs.get("pattern") or "").strip()
        recursive = kwargs.get("recursive", True)
        ignore_case = kwargs.get("ignore_case", False)
        user_id = str(context.get("user_id") or "")
        session_id = str(context.get("session_id") or "")

        if not path:
            return "[Tool Error] missing 'path' parameter"
        if not pattern:
            return "[Tool Error] missing 'pattern' parameter"

        try:
            matches = await self._gateway.grep_files(
                path, pattern,
                recursive=recursive if recursive is not None else True,
                ignore_case=ignore_case if ignore_case is not None else False,
                user_id=user_id, session_id=session_id,
            )
        except Exception as e:
            return f"[Tool Error] grep_files failed: {type(e).__name__}: {e}"

        if not matches:
            return f"No matches found for pattern '{pattern}' in {path}"

        lines = [f"Found {len(matches)} match(es) for '{pattern}':"]
        for m in matches[:50]:
            if isinstance(m, dict):
                fname = m.get("file", "?")
                lnum = m.get("line_number", 0)
                content = m.get("line", "")
                lines.append(f"  {fname}:{lnum}: {content}")
            else:
                lines.append(f"  {m}")
        if len(matches) > 50:
            lines.append(f"  ... ({len(matches) - 50} more matches)")
        return _truncate("\n".join(lines))


def _truncate(text: str) -> str:
    limit = settings.TOOL_RESULT_MAX_CHARS
    if limit and len(text) > limit:
        return text[:limit] + "\n...[truncated]..."
    return text

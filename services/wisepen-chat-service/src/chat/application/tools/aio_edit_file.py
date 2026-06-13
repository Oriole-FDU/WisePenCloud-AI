from typing import Any, Dict
from chat.core.providers.sandbox.aio_gateway_provider import AioGatewayProvider
from chat.application.tools.core.definition import (
    ToolDefinition, ToolLLMSpec, ToolParametersSchema, ToolPolicy,
)


class EditFileTool:
    def __init__(self, aio_gateway: AioGatewayProvider) -> None:
        self._gateway = aio_gateway

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="edit_file",
                description=(
                    "Exact string replacement in a file under /workspace/. "
                    "old_str must match exactly once (use read_file first to copy exact text)."
                ),
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "description": "File path under /workspace/"},
                        "old_str": {"type": "string", "description": "Exact text to replace"},
                        "new_str": {"type": "string", "description": "Replacement text"},
                    },
                    "required": ["file", "old_str", "new_str"],
                }),
            ),
            policy=ToolPolicy(expose_by_default=True),
        )

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        fp = str(kwargs.get("file") or "").strip()
        old = str(kwargs.get("old_str") or "")
        new = str(kwargs.get("new_str") or "")
        if not fp: return "[Tool Error] missing 'file'"
        if not old: return "[Tool Error] missing 'old_str'"
        uid = str(context.get("user_id") or "")
        sid = str(context.get("session_id") or "")
        try:
            r = await self._gateway.replace_in_file(fp, old, new, user_id=uid, session_id=sid)
        except Exception as e:
            return f"[Tool Error] edit_file failed: {type(e).__name__}: {e}"
        bw = r.get("bytes_written", 0) if isinstance(r, dict) else 0
        return f"Successfully edited {fp} ({bw} bytes written)"

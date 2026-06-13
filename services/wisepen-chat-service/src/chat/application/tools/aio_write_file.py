from typing import Any, Dict
from chat.core.providers.sandbox.aio_gateway_provider import AioGatewayProvider
from chat.application.tools.core.definition import (
    ToolDefinition, ToolLLMSpec, ToolParametersSchema, ToolPolicy,
)


class WriteFileTool:
    def __init__(self, aio_gateway: AioGatewayProvider) -> None:
        self._gateway = aio_gateway

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="write_file",
                description="Write content to a file in your sandbox workspace under /workspace/.",
                parameters_schema=ToolParametersSchema({
                    "type": "object",
                    "properties": {
                        "file": {"type": "string", "description": "File path under /workspace/"},
                        "content": {"type": "string", "description": "Content to write"},
                    },
                    "required": ["file", "content"],
                }),
            ),
            policy=ToolPolicy(expose_by_default=True),
        )

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        fp = str(kwargs.get("file") or "").strip()
        ct = str(kwargs.get("content") or "")
        if not fp: return "[Tool Error] missing 'file'"
        if not ct: return "[Tool Error] missing 'content'"
        uid = str(context.get("user_id") or "")
        sid = str(context.get("session_id") or "")
        try:
            r = await self._gateway.write_file(fp, ct, user_id=uid, session_id=sid)
        except Exception as e:
            return f"[Tool Error] write_file failed: {type(e).__name__}: {e}"
        return f"Successfully wrote {r.get('bytes_written', len(ct.encode('utf-8')))} bytes to {fp}"

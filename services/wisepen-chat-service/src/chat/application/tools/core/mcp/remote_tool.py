from __future__ import annotations

from typing import Any

from chat.application.tools.core import ToolDefinition, ToolExecutionError


class McpRemoteTool:
    def __init__(
        self,
        *,
        mcp_client: Any,
        server: Any,
        remote_name: str,
        definition: ToolDefinition,
        failure_reason: str,
    ) -> None:
        self._mcp_client = mcp_client
        self._server = server
        self._remote_name = remote_name
        self._definition = definition
        self._failure_reason = failure_reason

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        try:
            return await self._mcp_client.call_tool(self._server, self._remote_name, kwargs)
        except Exception as e:
            raise ToolExecutionError(
                reason=self._failure_reason,
                detail_reason=str(e),
                retryable=False,
            ) from e

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from chat.application.tools.core import ToolDefinition, ToolExecutionError
from chat.application.tools.core.output.tool_return import CacheableText, ToolReturn


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
    ) -> Any:
        try:
            if self._server is None:
                output = await self._mcp_client.call_tool(
                    self._server,
                    self._remote_name,
                    kwargs,
                    tool_config=config,
                    tool_context={
                        key: context[key]
                        for key in self._definition.policy.required_context_keys
                        if key in context
                    },
                    timeout_seconds=self._definition.policy.timeout_seconds,
                )
            else:
                output = await self._mcp_client.call_tool(
                    self._server,
                    self._remote_name,
                    kwargs,
                    timeout_seconds=self._definition.policy.timeout_seconds,
                )
            return _restore_tool_return(output)
        except Exception as e:
            raise ToolExecutionError(
                reason=self._failure_reason,
                detail_reason=str(e),
                retryable=False,
            ) from e


def _restore_tool_return(output: Any) -> Any:
    if not isinstance(output, Mapping):
        return output
    visible_result = output.get("visible_result")
    cacheable_texts = output.get("cacheable_texts")
    if not isinstance(visible_result, Mapping) or not isinstance(
        cacheable_texts, (list, tuple)
    ):
        return output
    return ToolReturn(
        visible_result=visible_result,
        cacheable_texts=tuple(
            CacheableText(
                text=str(item["text"]),
                is_md=bool(item.get("is_md", False)),
                metadata=(
                    item.get("metadata", {})
                    if isinstance(item.get("metadata", {}), Mapping)
                    else {}
                ),
            )
            for item in cacheable_texts
            if isinstance(item, Mapping) and "text" in item
        ),
    )

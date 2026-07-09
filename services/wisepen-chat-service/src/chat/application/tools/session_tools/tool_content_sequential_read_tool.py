from __future__ import annotations

from typing import Any

from chat.application.tools.common.tool_content_store import ToolContentStore
from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.session_tools.tool_content_read.models import (
    ToolContentSequentialReadResult,
)
from chat.application.tools.session_tools.tool_content_read.service import ToolContentReadService

TOOL_CONTENT_READ_TIMEOUT_SECONDS = 300.0

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_id": {
            "type": "string",
            "minLength": 1,
            "description": "Required. One cnt_* id from a previous content receipt.",
        },
        "offset": {
            "type": "integer",
            "default": 0,
            "description": "Optional. Character offset to start reading from.",
        },
        "limit": {
            "type": "integer",
            "default": 4000,
            "description": "Optional. Maximum number of characters to read.",
        },
    },
    "required": ["content_id"],
    "additionalProperties": False,
}


class ToolContentSequentialReadTool:
    """单文档顺序读取工具。"""

    __slots__ = ("_definition", "_service")

    def __init__(
            self,
            *,
            content_store: ToolContentStore,
            max_window_chars: int | None = None,
    ) -> None:
        self._service = ToolContentReadService(
            store=content_store,
            max_window_chars=max_window_chars,
        )
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_sequential_read",
                description=(
                    "Read one cached content_id sequentially by offset.\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when you need to continue reading a single cached content from a known offset.\n"
                    "  - SHOULD trigger when the user needs nearby surrounding context rather than cross-document search.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need natural-language retrieval across documents — use tool_content_rerank_read instead.\n"
                    "  - You need exact pattern matching across documents — use tool_content_regex_read instead.\n"
                    "  - You need new content from the web — use web_fetch or web_crawl instead.\n"
                ),
                parameters_schema=ToolParametersSchema(PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("session_id",),
                timeout_seconds=TOOL_CONTENT_READ_TIMEOUT_SECONDS,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> ToolContentSequentialReadResult:
        content_id = str(kwargs["content_id"])
        offset = int(kwargs.get("offset") or 0)
        limit = int(kwargs.get("limit") or 4000)

        try:
            # 顺序读取明确只接受单个 content_id；跨文档查找统一走专用读取工具。
            loaded = await self._service.load_stored_content(
                content_id=content_id,
                session_id=str(context["session_id"]),
            )
            if loaded is None:
                return ToolContentSequentialReadResult(
                    content_id=content_id,
                    status="failed",
                    reason="content_not_found",
                )

            canonical_id, stored = loaded
            return ToolContentSequentialReadResult(
                content_id=canonical_id,
                status="success",
                window=self._service.build_continuous_window(
                    stored=stored,
                    offset=offset,
                    limit=limit,
                ),
            )
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(
                reason="tool_content_sequential_read_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc

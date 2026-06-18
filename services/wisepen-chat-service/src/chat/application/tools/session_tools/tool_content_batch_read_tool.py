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
from chat.application.tools.session_tools.tool_content_batch_read.models import (
    ToolContentBatchReadItemRequest,
    ToolContentBatchReadRequest,
)
from chat.application.tools.session_tools.tool_content_batch_read.service import ToolContentBatchReadService

MAX_BATCH_READ_ITEMS = 8
MAX_CHUNK_INDICES_PER_ITEM = 8

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_BATCH_READ_ITEMS,
            "description": (
                "Required. Per-content chunk windows to expand. Each item binds one cnt_* id to "
                "its own chunk_indices; chunk indices are never shared across content_ids."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "content_id": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Required. One cnt_* id from a previous content receipt.",
                    },
                    "chunk_indices": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 1,
                        "maxItems": MAX_CHUNK_INDICES_PER_ITEM,
                        "description": (
                            "Required. One to eight center chunk_index values for this content_id. "
                            "Use chunk_index values from evidence_rank or previous read windows."
                        ),
                    },
                },
                "required": ["content_id", "chunk_indices"],
                "additionalProperties": False,
            },
        },
        "merge_before": {
            "type": "integer",
            "default": 0,
            "description": (
                "Number of chunks to include before each requested center chunk. Use the same value "
                "for all items in this call."
            ),
        },
        "merge_after": {
            "type": "integer",
            "default": 0,
            "description": (
                "Number of chunks to include after each requested center chunk. Use the same value "
                "for all items in this call."
            ),
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}


class ToolContentBatchReadTool:
    """显式 chunk 定位的批量读取工具。"""

    __slots__ = ("_definition", "_service")

    def __init__(
        self,
        *,
        content_store: ToolContentStore,
        service: ToolContentBatchReadService | None = None,
    ) -> None:
        self._service = service or ToolContentBatchReadService(store=content_store)
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_batch_read",
                description=(
                    "Expand exact chunk_index windows from cached ToolContent.\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when another tool (such as evidence_rank) already returned content_id plus chunk_index values.\n"
                    "  - SHOULD trigger when you have explicit chunk_index values from a previous read result.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You only have cnt_* content_ids without chunk_index values — use tool_content_read with ranked_expand or regex_match instead.\n"
                    "  - You need to fetch or parse new content — use web_fetch, web_crawl, or document_parse instead.\n"
                    "\n"
                    "INPUT RULES:\n"
                    "  - items MUST bind each content_id to its own chunk_indices; chunk indices are never shared across content_ids.\n"
                    "  - 1~8 items per call, each with 1~8 chunk_indices.\n"
                    "  - merge_before and merge_after apply uniformly to all items in this call.\n"
                    "\n"
                    "OUTPUT RULES:\n"
                    "  - Returns per-item window text with chunk metadata.\n"
                    "  - This tool only reads existing cnt_* content and never creates another content receipt.\n"
                ),
                parameters_schema=ToolParametersSchema(PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("session_id",),
                timeout_seconds=5.0,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        """返回工具的元定义（供框架注册使用）。"""
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> tuple[ToolContentBatchReadItemResult, ...]:
        """解析 per-content chunk 请求并返回普通结构化窗口结果。"""
        session_id = context["session_id"]

        items_payload = kwargs["items"]
        items = tuple(
            ToolContentBatchReadItemRequest(
                content_id=str(item["content_id"]),
                chunk_indices=tuple(int(value) for value in item["chunk_indices"]),
            )
            for item in items_payload
        )

        try:
            return await self._service.read(
                request=ToolContentBatchReadRequest(
                    items=items,
                    merge_before=int(kwargs.get("merge_before") or 0),
                    merge_after=int(kwargs.get("merge_after") or 0),
                ),
                session_id=str(session_id),
            )
        except ToolExecutionError:
            raise
        except Exception as e:
            raise ToolExecutionError(
                reason="tool_content_batch_read_failed",
                detail_reason=str(e),
                retryable=False,
            ) from e

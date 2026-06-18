from __future__ import annotations

from typing import Any

from common.logger import warn

from chat.application.tools.common.tool_content_store import ToolContentStore
from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.core.tool_return import SuggestedAction, SuggestedActionPriority
from chat.application.tools.session_tools.evidence_rank.service import EvidenceRankService
from chat.application.tools.tool_settings import tool_settings

MAX_CONTENT_IDS = tool_settings.EVIDENCE_RANK_MAX_CONTENT_IDS

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Required. The narrower second-pass information need used to rerank cached evidence."
            ),
        },
        "content_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": MAX_CONTENT_IDS,
            "description": (
                "Required. One to eight cnt_* ids from previous content receipts. "
                "All readable chunks across these content_ids are reranked together."
            ),
        },
        "max_evidence": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "default": 8,
            "description": "Maximum number of reranked evidence items to return.",
        },
    },
    "required": ["query", "content_ids"],
    "additionalProperties": False,
}


class EvidenceRankTool:
    """二次证据精排工具门面。"""

    __slots__ = ("_definition", "_service")

    def __init__(
        self,
        *,
        content_store: ToolContentStore,
        service: EvidenceRankService | None = None,
    ) -> None:
        self._service = service or EvidenceRankService(store=content_store)
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="evidence_rank",
                description=(
                    "Second-pass reranker for cached ToolContent evidence.\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when previous search/parse/read produced cnt_* content_ids and the next step is to rerank existing cached chunks with a narrower query.\n"
                    "  - SHOULD trigger when the current query is more specific than the one that produced the cached content.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You need to fetch URLs — use web_fetch or web_crawl instead.\n"
                    "  - You need to parse files — use document_parse instead.\n"
                    "  - You already have exact chunk_index values — use tool_content_batch_read instead.\n"
                    "\n"
                    "INPUT RULES:\n"
                    "  - query MUST be the narrower second-pass information need, not a generic word like 'details'.\n"
                    "  - content_ids MUST be 1~8 cnt_* ids from previous content receipts.\n"
                    "\n"
                    "OUTPUT RULES:\n"
                    "  - Returns ranked items with content_id and chunk_index values.\n"
                    "  - Pass the returned chunk_index values to tool_content_batch_read to expand windows.\n"
                    "  - This tool never fetches, parses, or creates new content receipts.\n"
                ),
                parameters_schema=ToolParametersSchema(PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("session_id",),
                timeout_seconds=tool_settings.EVIDENCE_RANK_TOOL_TIMEOUT_SECONDS,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any):
        content_ids = tuple(str(value) for value in kwargs["content_ids"])
        query = kwargs["query"].strip()

        try:
            result = await self._service.rank(
                query=query,
                content_ids=content_ids,
                session_id=str(context["session_id"]),
                max_evidence=int(kwargs.get("max_evidence") or tool_settings.EVIDENCE_RANK_DEFAULT_MAX_EVIDENCE),
            )
            # 建议动作属于工具返回边界：service 只产出排序定位，下一步读取策略由工具门面提示。
            return {
                "items": result,
                "suggested_action": SuggestedAction(
                    tool_name="tool_content_batch_read",
                    reason=(
                        "Directly expand ranked content_id and chunk_index results in per-content "
                        "batches without another ranking pass."
                    ),
                    priority=SuggestedActionPriority.HIGH,
                ),
            }
        except ToolExecutionError:
            raise
        except Exception as e:
            warn(
                "evidence rank failed.",
                e=e,
                content_ids=content_ids,
                max_evidence=int(kwargs.get("max_evidence") or tool_settings.EVIDENCE_RANK_DEFAULT_MAX_EVIDENCE),
                audit_message="证据精排服务失败，已包装为不可重试工具错误。",
            )
            raise ToolExecutionError(
                reason="evidence_rank_failed",
                detail_reason=str(e),
                retryable=False,
            ) from e

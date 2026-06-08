from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from chat.application.tools.common.evidence_ranking.formatting import format_evidence_result
from chat.application.tools.common.evidence_ranking.ranking import (
    MAX_CHUNKS_PER_CONTENT,
    rank_evidence,
)
from chat.application.tools.tool_content_store import ToolContentStore
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail

# 释放线程池并发潜能，将其扩展至合理的 CPU 核心边界，防止多 Agent 链并发吞吐时当场卡死
_EVIDENCE_RANK_EXECUTOR = ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix="evidence-rank",
)

TOOL_DESCRIPTION = (
    "Refined reranking entrypoint for cached tool content. web_search and web_fetch already "
    "perform default evidence ranking with objective when provided; evidence_rank is for "
    "second-pass ranking with a narrower, more specific, more verifiable query after initial "
    "search or page evidence has been inspected.\n\n"
    "Do not directly reuse a prior objective or ranking_query_used string as query. Focus the "
    "query around a term, error, API, version, constraint, contradiction, or source-specific "
    "question that needs reranking.\n\n"
    "Use evidence_rank for active second-pass reranking, cross-content_id ranking, debug, "
    "fallback, and cases that need term_hit_stats, matched_reason, offsets, or formatter "
    "guidance.\n\n"
    "Do not use evidence_rank to discover URLs, fetch pages, parse file_ref values, or continue "
    "reading from a known offset.\n"
    "Use tool_content_read for sequential continuation from next_offset.\n"
    "Use tool_content_read or tool_content_batch_read when more context is needed around returned "
    "content_id + chunk_index evidence.\n\n"
    "This tool returns ranked evidence snippets with excerpts. It does not fetch page bodies, "
    "parse files, or produce a final answer."
)

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "description": "The user's current question or concise information need for ranking.",
        },
        "content_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "description": "cnt_* ToolContent identifiers from previous tool results. Do not pass file_ref values.",
        },
        "max_evidence": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "description": "Maximum number of ranked evidence snippets to return.",
            "default": 8,
        },
        "before": {
            "type": "integer",
            "minimum": 0,
            "maximum": 2,
            "default": 0,
            "description": "Optional number of chunks before each hit to include as a lightweight context preview.",
        },
        "after": {
            "type": "integer",
            "minimum": 0,
            "maximum": 2,
            "default": 0,
            "description": "Optional number of chunks after each hit to include as a lightweight context preview.",
        },
        "max_context_chars_per_hit": {
            "type": "integer",
            "minimum": 200,
            "maximum": 4000,
            "default": 2000,
            "description": "Maximum characters of context preview per ranked hit.",
        },
    },
    "required": ["query", "content_ids"],
    "additionalProperties": False,
}


class EvidenceRankTool(BaseTool):
    def __init__(self, *, content_store: ToolContentStore) -> None:
        """初始化证据精排工具。"""
        self._content_store = content_store

    @property
    def name(self) -> str:
        return "evidence_rank"

    @property
    def description(self) -> str:
        return TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        """
        面向大模型/控制台调用的精排工具入口门面。
        采用非阻塞式专用线程池隔离密集的文本分词与 Fielding BM25 浮点计算。
        """
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        query = kwargs.get("query")
        if not isinstance(query, str) or not query:
            return "[Tool Error] query must be a non-empty string."

        content_ids = kwargs.get("content_ids")
        if not isinstance(content_ids, list) or not content_ids:
            return "[Tool Error] content_ids must be a non-empty list."

        max_evidence = kwargs.get("max_evidence", 8)
        if not isinstance(max_evidence, int) or max_evidence < 1:
            return "[Tool Error] max_evidence must be a positive integer."

        before = kwargs.get("before", 0)
        after = kwargs.get("after", 0)
        max_context_chars_per_hit = kwargs.get("max_context_chars_per_hit", 2000)
        if not isinstance(before, int) or not (0 <= before <= 2):
            return "[Tool Error] before must be an integer between 0 and 2."
        if not isinstance(after, int) or not (0 <= after <= 2):
            return "[Tool Error] after must be an integer between 0 and 2."
        if (
            not isinstance(max_context_chars_per_hit, int)
            or not (200 <= max_context_chars_per_hit <= 4000)
        ):
            return "[Tool Error] max_context_chars_per_hit must be an integer between 200 and 4000."

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                _EVIDENCE_RANK_EXECUTOR,
                rank_evidence,
                query,
                content_ids,
                session_id,
                self._content_store,
                max_evidence,
                MAX_CHUNKS_PER_CONTENT,
                before,
                after,
                max_context_chars_per_hit,
            )

            return format_evidence_result(result)

        except Exception as e:
            log_fail(
                "evidence_rank",
                repr(e),
                session_id=session_id,
                content_ids=content_ids,
            )
            return "[Tool Error] Unexpected error during evidence ranking."

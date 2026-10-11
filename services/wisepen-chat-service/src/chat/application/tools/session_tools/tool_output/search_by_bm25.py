from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common.utils.markdown import MarkdownChunk
from common.utils.retrieval import BM25Retriever, Candidate

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
    ToolSelectionMode,
    ToolUISpec,
)
from chat.application.tools.core.output_cache.cache_store import (
    StoredToolContent,
    get_tool_content,
)

_MAX_CONTENT_IDS = 16
_DEFAULT_TOP_K = 5
_MAX_TOP_K = 20
_TIMEOUT_SECONDS = 300.0

_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": _MAX_CONTENT_IDS,
            "description": "Cached tool output content IDs to search in the given order.",
        },
        "query": {
            "type": "string",
            "minLength": 1,
            "description": "Text query used for BM25 lexical retrieval.",
        },
        "top_k": {
            "type": "integer",
            "minimum": 1,
            "maximum": _MAX_TOP_K,
            "default": _DEFAULT_TOP_K,
            "description": "Number of relevant chunks to return in descending relevance order.",
        },
    },
    "required": ["content_ids", "query"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class ToolOutputBM25Chunk:
    """BM25 命中的自然大小 chunk 及其原文定位信息。"""

    content_id: str
    text: str
    start_offset: int | None
    end_offset: int | None
    section_id: str | None
    section_path: str


@dataclass(frozen=True, slots=True)
class _CachedChunk:
    """缓存检索 payload，保留会话内正文身份与原始 Markdown 定位。"""

    content_id: str
    chunk: MarkdownChunk


class SearchToolOutputBM25Tool:
    def __init__(self) -> None:
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="search_tool_output_bm25",
                description=(
                    "Search up to 16 cached tool outputs with BM25 lexical retrieval. "
                    "Return the most relevant natural-sized chunks in descending relevance order. "
                    "Each result includes the source content_id, original character offsets, "
                    "and section information for follow-up reading."
                ),
                parameters_schema=ToolParametersSchema(_PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=False,
                selection_mode=ToolSelectionMode.CONTEXTUAL,
                persist_output=True,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("session_id",),  # 缓存按会话隔离
                timeout_seconds=_TIMEOUT_SECONDS,
            ),
            ui_spec=ToolUISpec(
                display_name="BM25 搜索缓存的工具输出",
                description="BM25 lexical search across cached tool output chunks.",
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(
        self,
        context: dict[str, Any],
        config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[ToolOutputBM25Chunk]:
        del config  # 本工具无运行时配置
        try:
            query = kwargs["query"]
            if not query.strip():
                raise ToolExecutionError(
                    reason="bm25_query_empty",
                    retryable=False,
                )

            stored_items = await _load_stored_items(
                content_ids=kwargs["content_ids"],
                session_id=context["session_id"],
            )
            return await _search_chunks(
                stored_items=stored_items,
                query=query,
                top_k=kwargs["top_k"],
            )
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(
                reason="search_cached_tool_output_by_bm25_failed",
                detail_reason=str(exc),
                retryable=False,
            ) from exc


async def _load_stored_items(
    *,
    content_ids: list[str],
    session_id: str,
) -> list[StoredToolContent]:
    """按输入顺序加载当前会话可访问的缓存正文，并去除重复 ID。"""

    stored_items: list[StoredToolContent] = []
    for content_id in dict.fromkeys(content_ids):  # 去重并保持顺序
        stored = await get_tool_content(
            content_id=content_id,
            session_id=session_id,
        )
        # 不存在或不属于当前会话时静默跳过
        if stored is not None:
            stored_items.append(stored)
    return stored_items


async def _search_chunks(
    *,
    stored_items: list[StoredToolContent],
    query: str,
    top_k: int,
) -> list[ToolOutputBM25Chunk]:
    """将已加载正文的自然 chunk 交给公共 BM25 实现并投影结果。"""

    candidates = [
        _candidate(content_id=stored.content_id, chunk=chunk)
        for stored in stored_items
        for chunk in stored.chunks
    ]
    ranked = await BM25Retriever(
        candidates=candidates,
        text_getter=lambda payload: payload.chunk.text,
        top_k=top_k,
    ).retrieve(query)
    return [
        _result_chunk(
            content_id=candidate.payload.content_id,
            chunk=candidate.payload.chunk,
        )
        for candidate in ranked
    ]


def _candidate(*, content_id: str, chunk: MarkdownChunk) -> Candidate[_CachedChunk]:
    return Candidate(
        candidate_id=f"{content_id}:{chunk.chunk_index}",
        payload=_CachedChunk(content_id=content_id, chunk=chunk),
        rank=chunk.chunk_index + 1,
    )


def _result_chunk(*, content_id: str, chunk: MarkdownChunk) -> ToolOutputBM25Chunk:
    start_offset, end_offset = _chunk_offsets(chunk)
    return ToolOutputBM25Chunk(
        content_id=content_id,
        text=chunk.text,
        start_offset=start_offset,
        end_offset=end_offset,
        section_id=chunk.section_id,
        section_path=" > ".join(chunk.section_path),
    )


def _chunk_offsets(chunk: MarkdownChunk) -> tuple[int | None, int | None]:
    """把 chunk 的多个来源跨度合并成一个可继续读取的半开区间。"""

    if not chunk.source_spans:
        return None, None
    return (
        min(span.start_offset for span in chunk.source_spans),
        max(span.end_offset for span in chunk.source_spans),
    )

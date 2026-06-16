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
    ToolContentReadItemResult,
    ToolContentReadMode,
    ToolContentReadRequest,
    ToolContentSelector,
)
from chat.application.tools.session_tools.tool_content_read.service import ToolContentReadService

# JSON Schema：定义 LLM 可调用的 tool_content_read 工具参数
PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content_ids": {
            "type": "array",
            "items": {
                "type": "string",
                "minLength": 1,
            },
            "minItems": 1,
            "maxItems": 8,
            "description": (
                "Required. One to eight cnt_* ids from previous <content_receipt> values. "
                "The same mode, selector, offset, and window parameters apply to every content_id."
            ),
        },
        "mode": {
            "type": "string",
            "enum": [
                "continuous",       # 按字符偏移连续读取
                "ranked_expand",    # 按相关性排序后展开窗口
                "regex_match",      # 正则匹配后展开窗口
            ],
            "description": (
                "How to read the cached content. Use continuous to read by character offset, "
                "ranked_expand to search the content "
                "by a natural-language query, and regex_match to find exact patterns such as names, "
                "URLs, IDs, headings, or quoted phrases. Use tool_content_batch_read when you already "
                "have exact chunk_index values to expand."
            ),
        },
        "selector": {
            "type": "object",
            "description": (
                "Optional chunk prefilter applied before ranked_expand or regex_match. "
                "Use it only when the receipt or previous read result exposed useful structure. "
                "Multiple selector groups are intersected: for example sections plus unit_types means "
                "chunks in those sections AND with those unit types."
            ),
            "properties": {
                "unit_types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "description": (
                        "Restrict to chunks with these structural unit types, such as paragraph, "
                        "heading, list, table, code, image, or quote when available."
                    ),
                },
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "description": (
                        "Restrict to matching section names or section path fragments. "
                        "Use visible headings from previous read results or known document sections."
                    ),
                },
                "pages": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "description": (
                        "Restrict to page names or page labels when the cached content has page metadata."
                    ),
                },
                "anchors": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "description": (
                        "Restrict to named anchors such as table, figure, code block, or extracted "
                        "object names when available."
                    ),
                },
                "chunk_indices": {
                    "type": "array",
                    "items": {
                        "type": "integer"
                    },
                    "description": (
                        "Restrict to exact chunk indices reported by an earlier read result. "
                        "This is only a prefilter for ranked_expand or regex_match. Use "
                        "tool_content_batch_read to directly expand known chunk indices."
                    ),
                },
                "include_unknown": {  # 是否保留缺少结构元数据的 chunk
                    "type": "boolean",
                    "description": (
                        "When selecting by unit_types, keep chunks that do not have unit_type metadata. "
                        "Usually leave false to avoid broad noisy matches."
                    ),
                    "default": False,
                },
            },
        },
        "offset": {
            "type": "integer",
            "description": (
                "For continuous mode only. Character offset to start reading from. "
                "Use 0 for the beginning, or a start_offset from a previous window to continue."
            ),
        },
        "limit": {
            "type": "integer",
            "description": (
                "For continuous mode only. Maximum number of characters to read. "
                "Use a focused value when you only need nearby context."
            ),
        },
        "query": {
            "type": "string",
            "description": (
                "Required for ranked_expand. A natural-language query describing the information "
                "to find inside the cached content. Reuse the user's real information need, not "
                "generic words like 'details'."
            ),
        },
        "top_k": {
            "type": "integer",
            "description": (
                "For ranked_expand. Number of relevant windows to return before merge_before/"
                "merge_after expansion. Use a small value when you need focused evidence."
            ),
            "default": 5
        },
        "pattern": {
            "type": "string",
            "description": (
                "Required for regex_match. Python regular expression used to locate exact text "
                "patterns. Write the exact regex directly, including inline modifiers such as "
                "(?i) when needed. Do not use regex_match for vague keyword search; use ranked_expand."
            ),
        },
        "max_matches": {
            "type": "integer",
            "description": "For regex_match. Maximum number of matching windows to return.",
            "default": 10
        },
        "merge_before": {  # 以 center_chunk 为中心，向前扩展的 chunk 数
            "type": "integer",
            "description": (
                "For ranked_expand and regex_match. Number of chunks to include before "
                "each selected center chunk. Increase when the answer depends on preceding context."
            ),
            "default": 0
        },
        "merge_after": {  # 以 center_chunk 为中心，向后扩展的 chunk 数
            "type": "integer",
            "description": (
                "For ranked_expand and regex_match. Number of chunks to include after "
                "each selected center chunk. Increase when definitions, tables, or explanations continue."
            ),
            "default": 0
        },
    },
    "required": [
        "content_ids",
        "mode",
    ],
}

MAX_CONTENT_IDS = 8


class ToolContentReadTool:
    """读取 ToolContentStore 中已缓存工具内容的统一工具。

    对外暴露为 LLM 可调用的 tool（tool_content_read），
    支持三种读取模式：continuous / ranked_expand / regex_match。
    """

    __slots__ = ("_service", "_definition")

    def __init__(
        self,
        *,
        content_store: ToolContentStore,
    ) -> None:
        self._service = ToolContentReadService(store=content_store)
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="tool_content_read",
                description=(
                    "Read focused windows from cached tool output by content_ids.\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when a previous tool returned a <content_receipt> instead of full inline content.\n"
                    "  - SHOULD trigger when you need to inspect specific sections of cached content.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - You already have exact chunk_index values — use tool_content_batch_read instead.\n"
                    "  - You need to rerank cached chunks with a narrower query — use evidence_rank first.\n"
                    "  - You need new content from the web — use web_fetch or web_crawl instead.\n"
                    "\n"
                    "INPUT RULES:\n"
                    "  - content_ids MUST be 1~8 cnt_* ids from previous content receipts.\n"
                    "  - mode MUST be one of: continuous (offset read), ranked_expand (semantic search), regex_match (exact pattern).\n"
                    "  - query is required for ranked_expand; pattern is required for regex_match; offset/limit apply to continuous only.\n"
                    "  - selector optionally prefilters chunks by unit_types, sections, pages, anchors, or chunk_indices.\n"
                    "  - merge_before/merge_after expand windows around center chunks in ranked_expand and regex_match.\n"
                    "\n"
                    "OUTPUT RULES:\n"
                    "  - Returns window metadata plus readable text inline in each window.\n"
                    "  - This tool reads existing cnt_* content and never creates another content receipt.\n"
                ),
                parameters_schema=ToolParametersSchema(PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,      # 默认向 LLM 暴露
                persist_output=True,         # 输出需要持久化到会话记录
                risk_level=ToolRiskLevel.LOW,  # 低风险
                required_context_keys=("session_id",),  # 执行上下文必须包含 session_id
                timeout_seconds=5.0,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        """返回工具的元定义（供框架注册使用）。"""
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> tuple[ToolContentReadItemResult, ...]:
        """执行工具读取操作：解析请求 → 调用 Service → 返回普通结构化结果。"""
        session_id = context.get("session_id")
        if not session_id:
            raise ToolExecutionError(
                reason="missing session id",
                detail_reason="Missing session_id in execution context.",
            )

        try:
            # 解析 selector 负载：从 kwargs 的 selector dict 转为 Typed 的 ToolContentSelector
            selector_payload = kwargs.get("selector") or {}
            selector = ToolContentSelector(
                unit_types=tuple(selector_payload.get("unit_types") or ()),
                sections=tuple(selector_payload.get("sections") or ()),
                pages=tuple(selector_payload.get("pages") or ()),
                anchors=tuple(selector_payload.get("anchors") or ()),
                chunk_indices=tuple(int(value) for value in (selector_payload.get("chunk_indices") or ())),
                include_unknown=bool(selector_payload.get("include_unknown", False)),
            )
            # 组装内部请求对象
            content_ids = tuple(str(value) for value in kwargs["content_ids"])

            request = ToolContentReadRequest(
                content_ids=content_ids,
                mode=ToolContentReadMode(str(kwargs["mode"])),
                selector=selector,
                offset=kwargs.get("offset"),
                limit=kwargs.get("limit"),
                query=kwargs.get("query"),
                top_k=int(kwargs.get("top_k") or 5),
                pattern=kwargs.get("pattern"),
                max_matches=int(kwargs.get("max_matches") or 10),
                merge_before=int(kwargs.get("merge_before") or 0),
                merge_after=int(kwargs.get("merge_after") or 0),
            )
            # 调用 Service 执行读取
            return await self._service.read(request=request, session_id=session_id)

        except ToolExecutionError:
            raise

        except Exception as e:
            # 所有非 ToolExecutionError 异常统一包装为工具执行错误
            raise ToolExecutionError(
                reason="tool_content_read_failed",
                detail_reason=str(e),
                retryable=False,
            ) from e

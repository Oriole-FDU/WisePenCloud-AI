from __future__ import annotations

from typing import Any

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.core.tool_return import (
    SuggestedAction,
    SuggestedActionPriority,
    ToolReturn,
)
from chat.application.tools.web_tools.web_fetch import FetchCoordinator
from chat.application.tools.web_tools.web_fetch.errors import WebFetchError

MAX_URLS = 8

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "urls": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": MAX_URLS,
            "description": (
                "Required. Target URLs to fetch. Each MUST be a full http(s) URL."
            ),
        },
    },
    "required": ["urls"],
    "additionalProperties": False,
}


class WebFetchTool:
    """Web fetch 工具门面，批量抓取 URL。

    复用 FetchCoordinator 的 httpx → scrapling fallback 链路 + 清洗 + 质量判断。
    HTML 页面返回清洗后的 markdown；非 HTML 文件移交 ToolRunFileStore 返回 tfile_* 引用。
    单个 URL 失败不阻塞其他，转为 failed 项。

    与 web_crawl 的区别：
    - web_fetch 抓取一批独立 URL，URL 之间无关联
    - web_crawl 从种子 URL 出发递归爬取，自动发现并跟进链接
    """

    __slots__ = ("_definition", "_service")

    def __init__(
        self,
        *,
        service: FetchCoordinator,
    ) -> None:
        self._service = service
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="web_fetch",
                description=(
                    "Fetch one or more URLs in parallel and return cleaned markdown (HTML) or file references (non-HTML).\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when the user provides specific URL(s) and wants their content.\n"
                    "  - SHOULD trigger when search results surface concrete URLs that need to be read.\n"
                    "  - SHOULD trigger when the target is a known non-HTML file (PDF/image/office doc) — the tool will hand it off and return a file_ref for document_parse.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - The user only needs search candidates — use web_search instead.\n"
                    "  - Multiple related pages on the same site are needed — use web_crawl instead.\n"
                    "  - The URL is already fetched in this session — reuse the cached result instead of re-fetching.\n"
                    "\n"
                    "INPUT RULES:\n"
                    "  - urls MUST be full http(s) URLs, 1~8 items.\n"
                    "\n"
                    "OUTPUT RULES:\n"
                    "  - HTML page: returns title and cleaned markdown.\n"
                    "  - Non-HTML file: returns file_ref (tfile_*) and file_label; pass file_ref to document_parse to extract content.\n"
                    "  - Per-URL failure is returned in the failed list with a reason; do NOT silently drop failed URLs.\n"
                    "  - Within one session, do NOT re-fetch the same url unless new information is required.\n"
                ),
                parameters_schema=ToolParametersSchema(PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.MEDIUM,
                timeout_seconds=120.0,
                cache_chunked=True,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> ToolReturn:
        raw_urls = kwargs["urls"]
        if not isinstance(raw_urls, list) or not raw_urls:
            raise ToolExecutionError(
                reason="missing_urls",
                detail_reason="urls must be a non-empty array.",
                retryable=False,
            )
        if len(raw_urls) > MAX_URLS:
            raise ToolExecutionError(
                reason="too_many_urls",
                detail_reason=f"urls must have at most {MAX_URLS} items.",
                retryable=False,
            )

        urls: list[str] = []
        for u in raw_urls:
            url = str(u).strip()
            if not url:
                raise ToolExecutionError(
                    reason="invalid_url",
                    detail_reason="each url must be a non-empty string.",
                    retryable=False,
                )
            if not url.startswith(("http://", "https://")):
                raise ToolExecutionError(
                    reason="invalid_url",
                    detail_reason="each url must be a full http(s) URL.",
                    retryable=False,
                )
            urls.append(url)

        user_id = str(context.get("user_id") or "")
        session_id = str(context.get("session_id") or "")
        if not user_id or not session_id:
            raise ToolExecutionError(
                reason="missing_context",
                detail_reason="user_id and session_id are required in context.",
                retryable=False,
            )

        try:
            batch = await self._service.fetch_many(
                urls,
                user_id=user_id,
                session_id=session_id,
            )
        except WebFetchError as exc:
            raise ToolExecutionError(
                reason="web_fetch_failed",
                detail_reason=str(exc),
                retryable=True,
            ) from exc
        except Exception as exc:
            raise ToolExecutionError(
                reason="web_fetch_unexpected_error",
                detail_reason=str(exc),
                retryable=False,
            ) from exc

        # visible_result 直接放 dataclass，切面递归渲染
        # 有非 HTML 文件 → 建议 document_parse；否则建议 tool_content_read 检索 markdown
        has_file_ref = any(r.file_ref is not None for r in batch.items)
        if has_file_ref:
            suggested = SuggestedAction(
                tool_name="document_parse",
                reason="Parse the fetched non-HTML file(s) to extract their content.",
                priority=SuggestedActionPriority.HIGH,
            )
        else:
            suggested = SuggestedAction(
                tool_name="tool_content_read",
                mode="ranked_expand",
                reason="Search the fetched markdown for answer-relevant windows.",
                priority=SuggestedActionPriority.HIGH,
            )

        cacheable_texts = tuple(r.markdown for r in batch.items if r.markdown)

        return ToolReturn(
            tag="web_fetch_result",
            visible_result={
                "items": batch.items,
                "failed": batch.failed,
                "warnings": batch.warnings,
                "suggested_action": suggested,
            },
            cacheable_texts=cacheable_texts,
        )

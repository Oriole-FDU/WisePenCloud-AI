from __future__ import annotations

from typing import Any

from common.logger import warn

from chat.application.tools.common.batching import batched
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
    SuggestedActions,
    SuggestedActionPriority,
    ToolReturn,
)
from chat.application.tools.tool_settings import tool_settings
from chat.application.tools.web_tools.web_fetch import FetchCoordinator
from chat.application.tools.web_tools.web_fetch.errors import WebFetchError
from chat.application.tools.web_tools.web_fetch.models import WebFetchBatchResult
from chat.application.tools.web_tools.web_search.candidate_store.repository import (
    WebSearchCandidateRepository,
)

# --- 全局常量定义 ---
MAX_URLS = 64
SERVICE_BATCH_SIZE = 8

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["from_search_results", "from_direct_urls"],
            "description": "Required. Routing mode for fetch input interpretation.",
        },
        "urls": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": MAX_URLS,
            "description": (
                "Required. Target URLs to fetch. Each MUST be a full http(s) URL. "
                "Large sets are automatically split into internal batches."
            ),
        },
        "search_refs": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": MAX_URLS,
            "description": (
                "Alternative to urls. Search refs returned by web_search. "
                "Large sets are automatically split into internal batches."
            ),
        },
    },
    "required": ["mode"],
    "additionalProperties": False,
}


class WebFetchTool:
    """Web fetch 工具门面，批量抓取 URL。

    复用 FetchCoordinator 的 httpx -> scrapling fallback 链路 + 清洗 + 质量判断。
    HTML 页面返回清洗后的 markdown；非 HTML 文件移交 ToolRunFileStore 返回 tfile_* 引用。
    单个 URL 失败不阻塞其他，转为 failed 项。

    与 web_crawl 的区别：
    - web_fetch 抓取一批独立 URL，URL 之间无关联
    - web_crawl 从种子 URL 出发递归爬取，自动发现并跟进链接
    """

    __slots__ = ("_candidate_repository", "_definition", "_service")

    def __init__(
        self,
        *,
        service: FetchCoordinator,
        candidate_repository: WebSearchCandidateRepository,
    ) -> None:
        self._service = service
        self._candidate_repository = candidate_repository

        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="web_fetch",
                description=(
                    "Fetch one or more URLs in parallel and return cleaned markdown (HTML) or file references (non-HTML).\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when the user provides specific URL(s) and wants their content.\n"
                    "  - SHOULD trigger when search results surface concrete URLs that need to be read.\n"
                    "  - SHOULD trigger for normal HTML pages or when you are unsure whether a URL is HTML or a file.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - The user only needs search candidates — use web_search instead.\n"
                    "  - Multiple related pages on the same site are needed — use web_crawl instead.\n"
                    "  - The target is an obvious direct file URL (PDF/image/Office/spreadsheet/etc.) and the user needs document content — call document_parse with mode='from_direct_urls' instead.\n"
                    "  - The URL is already fetched in this session — reuse the cached result instead of re-fetching.\n"
                    "\n"
                    "INPUT RULES:\n"
                    "  - mode='from_direct_urls' => provide urls.\n"
                    "  - mode='from_search_results' => provide search_refs.\n"
                    "  - urls MUST be full http(s) URLs; large sets are auto-batched internally.\n"
                    "  - search_refs MUST come from a prior web_search result in this session; large sets are auto-batched internally.\n"
                    "\n"
                    "OUTPUT RULES:\n"
                    "  - HTML page: returns title and cleaned markdown.\n"
                    "  - Non-HTML file: returns file_ref (tfile_*) and file_label; pass file_ref to document_parse to extract content.\n"
                    "  - Avoid producing this file_ref handoff when the original user input was already an obvious direct file URL; document_parse can parse those URLs directly.\n"
                    "  - Per-URL failure is returned in the failed list with a reason; do NOT silently drop failed URLs.\n"
                    "  - Within one session, do NOT re-fetch the same url unless new information is required.\n"
                ),
                parameters_schema=ToolParametersSchema(PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.MEDIUM,
                timeout_seconds=tool_settings.WEB_FETCH_TOOL_TIMEOUT_SECONDS,
                cache_chunked=True,
                required_context_keys=("user_id", "session_id"),
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> ToolReturn:
        mode = kwargs["mode"]
        raw_urls = kwargs.get("urls")
        raw_search_refs = kwargs.get("search_refs")

        urls: list[str] = []
        source_scope = "web_public"

        # 1. 路由解析输入源模式
        match mode:
            case "from_direct_urls":
                if raw_urls is None:
                    raise ToolExecutionError(
                        reason="missing_urls",
                        detail_reason="urls is required when mode='from_direct_urls'.",
                        retryable=False,
                    )
                for u in raw_urls:
                    url = u.strip()
                    if not url.startswith(("http://", "https://")):
                        raise ToolExecutionError(
                            reason="invalid_url",
                            detail_reason="each url must be a full http(s) URL.",
                            retryable=False,
                        )
                    urls.append(url)

            case "from_search_results":
                if raw_search_refs is None:
                    raise ToolExecutionError(
                        reason="missing_search_refs",
                        detail_reason="search_refs is required when mode='from_search_results'.",
                        retryable=False,
                    )
                search_refs = tuple(item.strip() for item in raw_search_refs)
                urls, source_scope = await self._resolve_search_urls(
                    user_id=str(context["user_id"]),
                    search_refs=search_refs,
                )

            case _:
                raise ToolExecutionError(
                    reason="invalid_mode",
                    detail_reason="mode must be 'from_direct_urls' or 'from_search_results'.",
                    retryable=False,
                )

        # 2. 调用批量异步核心抓取服务
        user_id = str(context["user_id"])
        session_id = str(context["session_id"])

        try:
            batch = await self._fetch_batched(
                urls=tuple(urls),
                user_id=user_id,
                session_id=session_id,
                source_scope=source_scope,
            )
        except WebFetchError as exc:
            raise ToolExecutionError(
                reason="web_fetch_failed",
                detail_reason=str(exc),
                retryable=True,
            ) from exc
        except Exception as exc:
            warn(
                "web fetch unexpected error.",
                e=exc,
                mode=mode,
                urls=tuple(urls),
                source_scope=source_scope,
                audit_message="web_fetch 批量抓取发生未预期异常，已包装为不可重试 ToolExecutionError。",
            )
            raise ToolExecutionError(
                reason="web_fetch_unexpected_error",
                detail_reason=str(exc),
                retryable=False,
            ) from exc

        # 3. 动态计算下一步建议行为 (Suggested Actions)
        # 始终建议 tool_content_read；若存在文件类型引用则追加 document_parse
        has_file_ref = any(r.file_ref is not None for r in batch.items)
        action_list = [
            SuggestedAction(
                tool_name="tool_content_read",
                mode="ranked_expand",
                reason="Search the fetched markdown for answer-relevant windows.",
                priority=SuggestedActionPriority.HIGH,
            ),
        ]
        if has_file_ref:
            action_list.append(
                SuggestedAction(
                    tool_name="document_parse",
                    reason="Parse the fetched non-HTML file(s) to extract their content.",
                    priority=SuggestedActionPriority.HIGH,
                ),
            )
        suggested = SuggestedActions(suggested_actions=tuple(action_list))

        cacheable_texts = tuple(r.markdown for r in batch.items if r.markdown)

        # visible_result 中的 items 不能暴露 markdown，markdown 只能通过 cacheable_texts 走缓存
        visible_items = tuple(
            {
                "source_url": r.source_url,
                "final_url": r.final_url,
                "status_code": r.status_code,
                "content_type": r.content_type,
                "title": r.title,
                "warnings": r.warnings,
                "file_ref": r.file_ref,
                "file_label": r.file_label,
                "source_scope": r.source_scope,
            }
            for r in batch.items
        )

        return ToolReturn(
            tag="web_fetch_result",
            visible_result={
                "items": visible_items,
                "failed": batch.failed,
                "warnings": batch.warnings,
                "suggested_actions": suggested,
            },
            cacheable_texts=cacheable_texts,
        )

    async def _resolve_search_urls(
        self,
        *,
        user_id: str,
        search_refs: tuple[str, ...],
    ) -> tuple[list[str], str]:
        """将检索引用换算为实际抓取的真实 URL 路径。"""
        urls: list[str] = []
        source_scope: str | None = None

        for search_ref in search_refs:
            mapping = await self._candidate_repository.get_mapping(
                user_id=user_id,
                search_ref=search_ref,
            )
            if mapping is None:
                raise ToolExecutionError(
                    reason="search_ref_not_found",
                    detail_reason="search_refs must come from a prior web_search result for this user.",
                    retryable=False,
                )
            urls.append(mapping.url)
            if source_scope is None:
                source_scope = mapping.source_scope
            elif source_scope != mapping.source_scope:
                # 同一批抓取只允许一个缓存访问域，避免 public/custom 混写污染缓存。
                raise ToolExecutionError(
                    reason="mixed_search_ref_source_scope",
                    detail_reason="search_refs in one web_fetch call must share the same source scope.",
                    retryable=False,
                )

        return urls, source_scope or "web_public"

    async def _fetch_batched(
        self,
        *,
        urls: tuple[str, ...],
        user_id: str,
        session_id: str,
        source_scope: str,
    ) -> WebFetchBatchResult:
        items = []
        failed = []
        warnings: list[str] = []
        for url_batch in batched(urls, batch_size=SERVICE_BATCH_SIZE):
            batch = await self._service.fetch_many(
                list(url_batch),
                user_id=user_id,
                session_id=session_id,
                source_scope=source_scope,
            )
            items.extend(batch.items)
            failed.extend(batch.failed)
            warnings.extend(batch.warnings)
        return WebFetchBatchResult(
            items=tuple(items),
            failed=tuple(failed),
            warnings=tuple(warnings),
        )

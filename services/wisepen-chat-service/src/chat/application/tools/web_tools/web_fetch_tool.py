from __future__ import annotations

from typing import Any

from common.logger import warn

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
from chat.application.tools.web_tools.web_search.candidate_store.repository import (
    WebSearchCandidateRepository,
)

# --- 全局常量定义 ---
MAX_URLS = 8

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
            "description": "Required. Target URLs to fetch. Each MUST be a full http(s) URL.",
        },
        "search_refs": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": MAX_URLS,
            "description": "Alternative to urls. Search refs returned by web_search.",
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
                    "  - SHOULD trigger when the target is a known non-HTML file (PDF/image/office doc) — the tool will hand it off and return a file_ref for document_parse.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - The user only needs search candidates — use web_search instead.\n"
                    "  - Multiple related pages on the same site are needed — use web_crawl instead.\n"
                    "  - The URL is already fetched in this session — reuse the cached result instead of re-fetching.\n"
                    "\n"
                    "INPUT RULES:\n"
                    "  - mode='from_direct_urls' => provide urls.\n"
                    "  - mode='from_search_results' => provide search_refs.\n"
                    "  - urls MUST be full http(s) URLs, 1~8 items.\n"
                    "  - search_refs MUST come from a prior web_search result in this session.\n"
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
                search_refs = self._parse_search_refs(raw_search_refs)
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
            batch = await self._service.fetch_many(
                urls,
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

        # 3. 动态计算下一步建议行为 (Suggested Action)
        # 只要有一项是文件类型引用，就建议进行文件深度解析；否则进行文本窗口读取检索
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

        return urls, source_scope or "web_public"

    @staticmethod
    def _parse_search_refs(raw_search_refs: Any) -> tuple[str, ...]:
        if raw_search_refs is None:
            raise ToolExecutionError(
                reason="missing_search_refs",
                detail_reason="search_refs is required when mode='from_search_results'.",
                retryable=False,
            )
        return tuple(item.strip() for item in raw_search_refs)

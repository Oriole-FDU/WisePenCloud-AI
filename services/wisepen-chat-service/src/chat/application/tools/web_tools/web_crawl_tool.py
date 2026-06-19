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
    SuggestedActions,
    SuggestedActionPriority,
    ToolReturn,
)
from chat.application.tools.tool_settings import tool_settings
from chat.application.tools.web_tools.web_fetch import WebCrawlService
from chat.application.tools.web_tools.web_fetch.errors import WebFetchError

# --- 全局常量限制（通过 tool_settings 调参控制）---
DEFAULT_MAX_PAGES = tool_settings.WEB_CRAWL_DEFAULT_MAX_PAGES
DEFAULT_MAX_DEPTH = tool_settings.WEB_CRAWL_DEFAULT_MAX_DEPTH
MAX_MAX_PAGES = tool_settings.WEB_CRAWL_MAX_MAX_PAGES
MAX_MAX_DEPTH = tool_settings.WEB_CRAWL_MAX_MAX_DEPTH

PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "seed_url": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Required. The seed URL to start crawling from. MUST be a full http(s) URL."
            ),
        },
        "max_pages": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_MAX_PAGES,
            "default": DEFAULT_MAX_PAGES,
            "description": (
                "Maximum number of pages to crawl (including the seed page). "
                "SHOULD be left at default unless the user explicitly needs broader coverage."
            ),
        },
        "max_depth": {
            "type": "integer",
            "minimum": 0,
            "maximum": MAX_MAX_DEPTH,
            "default": DEFAULT_MAX_DEPTH,
            "description": (
                "Maximum crawl depth. Seed page is depth 0. "
                "SHOULD be left at default; increase only when the user needs deeper traversal."
            ),
        },
        "same_domain": {
            "type": "boolean",
            "default": True,
            "description": (
                "Whether to restrict crawling to the same domain as the seed URL. "
                "SHOULD be left True unless the user explicitly asks for cross-domain crawling."
            ),
        },
    },
    "required": ["seed_url"],
    "additionalProperties": False,
}


class WebCrawlTool:
    """Web crawl 工具门面，递归爬取同域 HTML 页面。

    复用 FetchCoordinator 的 fetcher 链路（httpx -> scrapling fallback）+ cleaner，
    用 lxml 从 raw_html 提取链接，BFS 递归爬取。

    与 web_fetch 的区别：
    - web_fetch 抓取单个 URL，返回单页结果
    - web_crawl 从种子 URL 出发递归爬取，返回多页结果集合

    非 HTML 文件（PDF/图片等）在 crawl 中被跳过（不递归、不 handoff），
    因为 crawl 的目标是 HTML 页面集合，文件抓取应使用 web_fetch。
    """

    __slots__ = ("_definition", "_service")

    def __init__(
            self,
            *,
            service: WebCrawlService,
    ) -> None:
        self._service = service
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="web_crawl",
                description=(
                    "Recursively crawl HTML pages starting from a seed URL, returning cleaned markdown for each page.\n"
                    "\n"
                    "WHEN TO TRIGGER:\n"
                    "  - MUST trigger when the user needs to gather content from multiple related pages on the same site.\n"
                    "  - SHOULD trigger when the user asks to 'crawl', 'scrape a site', 'collect pages from', or 'read the whole section'.\n"
                    "  - SHOULD trigger when a single web_fetch is insufficient and the user points at an entry page for deeper content.\n"
                    "DO NOT TRIGGER when:\n"
                    "  - A single page is enough — use web_fetch instead.\n"
                    "  - The user only needs search candidates — use web_search instead.\n"
                    "  - The target is a known non-HTML file (PDF/image) — use web_fetch instead.\n"
                    "\n"
                    "INPUT RULES:\n"
                    "  - seed_url MUST be a full http(s) URL.\n"
                    "  - max_pages and max_depth SHOULD be left at default unless the user explicitly requests broader/deeper coverage.\n"
                    "  - same_domain SHOULD be True unless the user explicitly asks for cross-domain crawling.\n"
                    "\n"
                    "OUTPUT RULES:\n"
                    "  - Returns a list of crawled pages with cleaned markdown content.\n"
                    "  - Pages that failed to fetch or were non-HTML are skipped (not in the result).\n"
                    "  - If no pages could be fetched, inform the user; do NOT silently return empty results.\n"
                    "  - Within one session, do NOT re-crawl the same seed_url unless new information is required.\n"
                ),
                parameters_schema=ToolParametersSchema(PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.MEDIUM,
                timeout_seconds=tool_settings.WEB_CRAWL_TOOL_TIMEOUT_SECONDS,
                cache_chunked=True,
                required_context_keys=("user_id", "session_id"),
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> ToolReturn:
        # 1. 参数提取与前置合法性校验
        seed_url = kwargs["seed_url"].strip()
        max_pages = kwargs.get("max_pages") or DEFAULT_MAX_PAGES
        max_depth = kwargs.get("max_depth") or DEFAULT_MAX_DEPTH
        same_domain = kwargs.get("same_domain") if kwargs.get("same_domain") is not None else True

        if not seed_url.startswith(("http://", "https://")):
            raise ToolExecutionError(
                reason="invalid_seed_url",
                detail_reason="seed_url must be a full http(s) URL.",
                retryable=False,
            )

        # 2. 调度异步批量递归爬虫服务
        try:
            results = await self._service.crawl(
                seed_url,
                user_id=str(context["user_id"]),
                session_id=str(context["session_id"]),
                source_scope="web_public",
                max_pages=max_pages,
                max_depth=max_depth,
                same_domain=same_domain,
            )
        except WebFetchError as exc:
            raise ToolExecutionError(
                reason="web_crawl_failed",
                detail_reason=str(exc),
                retryable=True,
            ) from exc
        except Exception as exc:
            warn(
                "web crawl unexpected error.",
                e=exc,
                seed_url=seed_url,
                max_pages=max_pages,
                max_depth=max_depth,
                same_domain=same_domain,
                audit_message="web_crawl 抓取发生未预期异常，已包装为不可重试 ToolExecutionError。",
            )
            raise ToolExecutionError(
                reason="web_crawl_unexpected_error",
                detail_reason=str(exc),
                retryable=False,
            ) from exc

        # 3. 结果空值防御
        if not results:
            raise ToolExecutionError(
                reason="web_crawl_empty_result",
                detail_reason="No pages could be crawled from the seed URL.",
                retryable=True,
            )

        # 4. 构造可见结果：提取精简页面摘要列表
        pages_summary = [
            {
                "url": r.source_url,
                "final_url": r.final_url,
                "title": r.title,
                "markdown_length": len(r.markdown or ""),
                "warnings": list(r.warnings) if r.warnings else [],
            }
            for r in results
        ]

        # 转换可缓存文本集合，供上层切面提取并建立索引分块
        cacheable_texts = tuple(r.markdown for r in results if r.markdown)

        return ToolReturn(
            tag="web_crawl_result",
            visible_result={
                "seed_url": seed_url,
                "pages_crawled": len(results),
                "pages": pages_summary,
                "suggested_actions": SuggestedActions(
                    suggested_actions=(
                        SuggestedAction(
                            tool_name="tool_content_read",
                            mode="ranked_expand",
                            reason="Search the crawled pages' markdown for answer-relevant windows.",
                            priority=SuggestedActionPriority.HIGH,
                        ),
                    ),
                ),
            },
            cacheable_texts=cacheable_texts,
        )

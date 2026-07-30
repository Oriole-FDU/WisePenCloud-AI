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
from chat.application.tools.core.output.tool_return import CacheableText, ToolReturn

from .web_fetch import WebCrawler

DEFAULT_MAX_PAGES = 20
DEFAULT_MAX_DEPTH = 2
MAX_MAX_PAGES = 100
MAX_MAX_DEPTH = 5

_PARAMETERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "seed_url": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Complete public http:// or https:// URL of the first HTML page to read. "
                "This is a starting page, not a search query or site name."
            ),
        },
        "max_pages": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_MAX_PAGES,
            "default": DEFAULT_MAX_PAGES,
            "description": (
                "Maximum number of successfully fetched HTML pages to return, including the "
                "seed page. Lower this when only a small site section is needed."
            ),
        },
        "max_depth": {
            "type": "integer",
            "minimum": 0,
            "maximum": MAX_MAX_DEPTH,
            "default": DEFAULT_MAX_DEPTH,
            "description": (
                "Maximum link distance from the seed page: 0 reads only the seed, 1 also reads "
                "its direct links, and so on."
            ),
        },
        "same_domain": {
            "type": "boolean",
            "default": True,
            "description": (
                "When true, follow only links on the seed URL's domain (recommended for focused "
                "site crawling). Set false only when linked pages on other domains are part of the "
                "requested source set."
            ),
        },
    },
    "required": ["seed_url"],
    "additionalProperties": False,
}


class WebCrawlTool:
    __slots__ = ("_crawler", "_definition")

    def __init__(self, *, crawler: WebCrawler) -> None:
        self._crawler = crawler
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="web_crawl",
                description=(
                    "Discover and fetch multiple linked HTML pages beginning at one public seed URL. "
                    "Use this when the exact pages are not all known and the answer may span a site "
                    "section. Pages are discovered breadth-first from links in fetched HTML, subject "
                    "to max_pages, max_depth, and same_domain; the result contains only successfully "
                    "cleaned HTML pages as Markdown. This tool does not extract direct PDF URLs or "
                    "search the web. Use web_fetch for one known page, a batch of known URLs, or a "
                    "direct PDF URL. Each returned page includes its source URL, and the cached "
                    "content keeps source_url metadata for follow-up content reads."
                ),
                parameters_schema=ToolParametersSchema(_PARAMETERS_SCHEMA),
            ),
            policy=ToolPolicy(
                expose_by_default=True,
                persist_output=True,
                risk_level=ToolRiskLevel.MEDIUM,
                timeout_seconds=300.0,
                required_context_keys=("user_id",),
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
    ) -> ToolReturn:
        del config
        seed_url = str(kwargs["seed_url"]).strip()
        try:
            pages = await self._crawler.crawl(
                seed_url,
                user_id=str(context["user_id"]),
                max_pages=int(kwargs.get("max_pages") or DEFAULT_MAX_PAGES),
                max_depth=int(kwargs.get("max_depth") or DEFAULT_MAX_DEPTH),
                same_domain=(
                    kwargs.get("same_domain")
                    if kwargs.get("same_domain") is not None
                    else True
                ),
            )
        except Exception as exc:
            raise ToolExecutionError(
                reason="web_crawl_failed",
                detail_reason=str(exc),
                retryable=True,
            ) from exc

        if not pages:
            raise ToolExecutionError(
                reason="web_crawl_empty_result",
                detail_reason="No HTML pages could be crawled from the seed URL.",
                retryable=True,
            )

        visible_result = {
            "seed_url": seed_url,
            "pages_crawled": len(pages),
            "pages": tuple(
                {"url": page.source_url}
                for page in pages
            ),
        }
        return ToolReturn(
            visible_result=visible_result,
            cacheable_texts=tuple(
                CacheableText(
                    text=page.text,
                    is_md=True,
                    metadata={"source_url": page.source_url},
                )
                for page in pages
            ),
        )

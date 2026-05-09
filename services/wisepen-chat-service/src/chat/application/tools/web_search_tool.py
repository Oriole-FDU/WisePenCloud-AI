from __future__ import annotations

from typing import Any, Dict, Optional

from chat.application.web_search import SearchResponse, create_search_coordinator
from chat.application.web_search.search_coordinator import SearchCoordinator
from chat.application.web_search.models import ImageResult
from chat.application.web_search.utils import count_unique_domains, extract_domain
from chat.application.web_fetch.fetch_coordinator import FetchCoordinator
from chat.core.config.app_settings import settings
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail

__all__ = [
    "WebSearchTool",
]

TRUNCATION_MARKER = "\n\n...(Search result truncated due to length)"

TOOL_DESCRIPTION = (
    "Searches the web using a staged fallback chain: "
    "fresh cache, SearXNG, DuckDuckGo buffer, stale cache, then Tavily as paid fallback.\n\n"
    "Use this tool when current information, external facts, source lookup, or web evidence is required.\n\n"
    "Use freshness_required=true when the user asks for time-sensitive information, "
    "such as latest news, current facts, recent releases, current office holders, prices, weather, scores, schedules, or events happening now.\n\n"
    "Use with_images=true when the user asks for pictures, photos, visual references, "
    "locations, people, animals, products, UI screenshots, or other visual information.\n\n"
    "Use fetch_top_pages=true only when snippets are insufficient and the user needs a detailed source-backed answer."
)

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The web search query string.",
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum number of web results to return. Default is 5. Maximum is 10.",
            "default": 5,
            "minimum": 1,
            "maximum": 10,
        },
        "with_images": {
            "type": "boolean",
            "description": (
                "Whether to include relevant image results. "
                "Use this when the user asks for pictures, photos, visual references, locations, "
                "people, products, animals, screenshots, or other visual information."
            ),
            "default": False,
        },
        "freshness_required": {
            "type": "boolean",
            "description": (
                "Whether the search must avoid stale cached results. "
                "Set to true for time-sensitive queries such as latest news, current facts, prices, "
                "weather, scores, schedules, recent releases, or current office holders."
            ),
            "default": False,
        },
        "fetch_top_pages": {
            "type": "boolean",
            "description": (
                "Whether to fetch and extract content from the top 1-2 result pages. "
                "Use this when the user asks for detailed summary, source-backed answer, "
                "or when snippets are insufficient."
            ),
            "default": False,
        },
    },
    "required": ["query"],
}


class WebSearchTool(BaseTool):
    def __init__(self, coordinator: Optional[SearchCoordinator] = None):
        self._coordinator = coordinator or create_search_coordinator()
        self._fetcher = FetchCoordinator(settings.STEEL_BASE_URL)

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        query = kwargs.get("query", "").strip()
        if not query:
            return "[Tool Error] Missing required query parameter."

        max_results = kwargs.get("max_results", 5)
        max_results = max(1, min(max_results, 10))

        with_images = kwargs.get("with_images", False)
        freshness_required = kwargs.get("freshness_required", False)
        fetch_top_pages = kwargs.get("fetch_top_pages", False)

        try:
            response = await self._coordinator.search(
                query=query,
                max_results=max_results,
                with_images=with_images,
                freshness_required=freshness_required,
            )
        except Exception as e:
            log_fail(
                "联网搜索工具",
                e,
                session_id=session_id,
                query=query,
                max_results=max_results,
                with_images=with_images,
                freshness_required=freshness_required,
            )
            return "[Tool Error] Unexpected error while searching the web."

        if response is None:
            return "[Tool Result] Failed to search the web (all search methods exhausted)."

        if not has_search_content(response):
            return "[Tool Result] No results found for the query."

        extra_contents: list[str] = []

        if fetch_top_pages:
            extra_contents = await self._fetch_top_pages(response, limit=2)

        return format_response(response, extra_contents=extra_contents)

    async def _fetch_top_pages(
        self,
        response: SearchResponse,
        *,
        limit: int = 2,
    ) -> list[str]:
        contents: list[str] = []

        for result in response.results[:limit]:
            url = result.url.strip()
            if not url:
                continue

            try:
                content = await self._fetcher.fetch(url)
            except Exception:
                continue

            if not content:
                continue

            contents.append(
                f"Fetched page: {url}\n"
                f"{content[:3000]}"
            )

        return contents


def has_search_content(response: SearchResponse) -> bool:
    return bool(response.answer or response.results or response.images)


def format_response(
    response: SearchResponse,
    *,
    extra_contents: list[str] | None = None,
) -> str:
    unique_domains = count_unique_domains(tuple(response.results))

    lines = [f"[Tool Result] Web search results for: {response.query}"]

    if response.source:
        lines.append(f"Source: {response.source}")

    lines.append(
        f"Summary: {len(response.results)} results, "
        f"{len(response.images)} query-level images, "
        f"{unique_domains} unique domains."
    )

    if response.source == "stale_cache":
        lines.append(
            "Note: These results came from stale cache and may be outdated."
        )

    if response.answer:
        lines.append(f"\nAnswer:\n{response.answer}")

    if response.results:
        lines.append("\nResults:")

    for index, result in enumerate(response.results, 1):
        title = result.title.strip() or result.url or "(no title)"
        url = result.url.strip()
        snippet = result.snippet.strip()
        domain = extract_domain(url)

        lines.append(f"\n{index}. {title}")

        if domain:
            lines.append(f"   Domain: {domain}")

        if url:
            lines.append(f"   URL: {url}")

        if snippet:
            lines.append(f"   Snippet: {snippet}")

        if result.images:
            lines.append("   Images:")
            for image in result.images[:2]:
                lines.append(format_image_line(image, indent="      "))

    if response.images:
        lines.append("\nQuery-level images:")
        for image in response.images[:5]:
            lines.append(format_image_line(image, indent="   "))

    if extra_contents:
        lines.append("\nFetched top pages:")
        for index, content in enumerate(extra_contents, 1):
            lines.append(f"\n--- Page {index} ---")
            lines.append(content)

    return normalize_search_result("\n".join(lines))


def format_image_line(image: ImageResult, *, indent: str) -> str:
    url = image.url.strip()
    desc = image.desc.strip() if image.desc else ""

    line = f"{indent}- {url}"

    details: list[str] = []

    if desc:
        details.append(desc)

    if image.resolution:
        details.append(f"resolution={image.resolution}")

    if image.source_url:
        details.append(f"source={image.source_url}")

    if details:
        line += f" ({'; '.join(details)})"

    return line


def normalize_search_result(result: str) -> str:
    result = result.strip()

    if len(result) > settings.TOOL_RESULT_MAX_CHARS:
        limit = settings.TOOL_RESULT_MAX_CHARS
        keep_len = max(0, limit - len(TRUNCATION_MARKER))
        result = result[:keep_len].rstrip() + TRUNCATION_MARKER

    return result

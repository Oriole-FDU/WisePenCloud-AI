from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from wisepen_mcp.capabilities.core.tool_metadata import get_tool_config_value
from wisepen_mcp.service_client import ScholarClient


class SearchField(StrEnum):
    GENERAL = "general"
    COMPUTER_SCIENCE = "computer_science"
    MEDICINE = "medicine"
    BIOLOGY = "biology"
    ENGINEERING = "engineering"
    ECONOMICS_BUSINESS = "economics_business"
    MATHEMATICS = "mathematics"
    PHYSICS = "physics"
    CHEMISTRY = "chemistry"
    PSYCHOLOGY = "psychology"
    SOCIAL_SCIENCES = "social_sciences"
    ARTS_HUMANITIES = "arts_humanities"
    LAW = "law"
    MATERIALS_ENVIRONMENTAL_SCIENCES = "materials_environmental_sciences"


class SearchSourceScope(StrEnum):
    DEFAULT = "default"
    MULTI_SOURCE = "multi_source"


class DiscoveryField(StrEnum):
    COMPUTER_SCIENCE = "computer_science"


class DiscoveryPeriod(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class DiscoverySort(StrEnum):
    RECENT = "recent"
    TRENDING = "trending"


def register_scholar_tools(mcp: FastMCP, scholar_client: ScholarClient) -> None:
    @mcp.tool(
        name="search_papers",
        description=(
            "Search academic papers across scholarly providers. "
            "Use this tool when you already have a clear research topic, specific technical concepts, or scholarly keywords. "
            "If you need to explore or clarify an unfamiliar research direction first, use web_search before calling this tool. "
            "Sources: Built upon three general platforms (arXiv is always available without keys; OpenAlex and Semantic Scholar platform keys are reserved for metadata enrichment, so direct searching on them requires user BYOK) "
            "plus domain-specific sources (some require user BYOK for search). "
            "Scholar handles query normalization, multi-source routing, and metadata fusion. "
            "Results already include configured field enrichment and, when available, "
            "metadata.arxiv.categories, raw journal_ref, and author comments. "
            "journal_ref and comments are source text, not normalized publication or peer-review status."
        ),
    )
    async def search_papers(
        ctx: Context,
        query: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "Targeted academic search query or research topic. "
                    "Use precise scholarly terms or keywords rather than vague conversational questions."
                ),
            ),
        ],
        field: Annotated[
            SearchField,
            Field(
                description=(
                    "Academic discipline used for routing and domain-specific sources "
                    "(e.g., 'computer_science', 'medicine', 'physics'). Defaults to 'general'."
                )
            ),
        ] = SearchField.GENERAL,
        published_after: Annotated[
            date | None,
            Field(
                description="Inclusive lower bound for publication date (YYYY-MM-DD)."
            ),
        ] = None,
        published_before: Annotated[
            date | None,
            Field(
                description="Inclusive upper bound for publication date (YYYY-MM-DD)."
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(
                ge=1,
                le=20,
                description="Maximum number of papers to retrieve (1 to 20, default 10).",
            ),
        ] = 10,
        source_scope: Annotated[
            SearchSourceScope,
            Field(
                description=(
                    "Source execution scope: 'default' queries the primary source with fallback; "
                    "'multi_source' queries multiple sources concurrently and fuses/deduplicates results."
                )
            ),
        ] = SearchSourceScope.DEFAULT,
    ) -> dict[str, Any]:
        return await scholar_client.search_papers(
            query=query,
            field=field.value,
            published_after=published_after,
            published_before=published_before,
            limit=limit,
            source_scope=source_scope.value,
            openalex_api_key=_secret(ctx, "openalex_api_key"),
            huggingface_api_key=_secret(ctx, "huggingface_api_key"),
            s2_api_key=_secret(ctx, "s2_api_key"),
        )

    @mcp.tool(
        name="lookup_papers",
        description=(
            "Retrieve 1 to 20 specific papers by known identifiers (DOI, arXiv ID, etc.). "
            "Returns the same paper information and configured field enrichment as search_papers; "
            "use it for exact identifier queries, not as routine post-processing to obtain fuller search results. "
            "IMPORTANT: Batch as many identifiers as possible in a single call (up to 20) instead of calling this tool one-by-one in a loop, "
            "to minimize network roundtrips and avoid rate-limiting by scholarly providers. "
            "Scholar normalizes identifiers and stops general-source fallback when an exact paper match is found."
        ),
    )
    async def lookup_papers(
        ctx: Context,
        identifiers: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=20,
                description=(
                    "List of 1 to 20 paper identifiers (e.g., DOI like '10.1145/...', arXiv ID like '2301.12345' or 'arxiv:2301.12345'). "
                    "Always batch all known identifiers into a single request."
                ),
            ),
        ],
        field: Annotated[
            SearchField,
            Field(
                description=(
                    "Academic discipline used for routing and domain-specific source enrichment, "
                    "with the same field semantics as search_papers. Defaults to 'general'."
                )
            ),
        ] = SearchField.GENERAL,
    ) -> dict[str, Any]:
        return await scholar_client.lookup_papers(
            identifiers=identifiers,
            field=field.value,
            openalex_api_key=_secret(ctx, "openalex_api_key"),
            huggingface_api_key=_secret(ctx, "huggingface_api_key"),
            s2_api_key=_secret(ctx, "s2_api_key"),
        )

    @mcp.tool(
        name="discover_papers",
        description=(
            "Discover recent or trending papers within a specific academic field over a given time window (daily/weekly/monthly). "
            "Designed exclusively for temporal exploration, tracking research community buzz, and radar feeds. "
            "DO NOT use for targeted or goal-oriented literature retrieval — use search_papers instead when searching for specific topics or keywords. "
            "Discovery is powered by domain-specific sources (which may require user-configured API keys)."
        ),
    )
    async def discover_papers(
        ctx: Context,
        field: Annotated[
            DiscoveryField,
            Field(
                description="Academic field with registered discovery feed support (e.g., 'computer_science')."
            ),
        ],
        period: Annotated[
            DiscoveryPeriod,
            Field(
                description="Time window for the discovery feed: 'daily', 'weekly', or 'monthly'."
            ),
        ] = DiscoveryPeriod.DAILY,
        sort: Annotated[
            DiscoverySort,
            Field(
                description="Ranking criterion: 'trending' (community interest/activity) or 'recent' (latest publication date)."
            ),
        ] = DiscoverySort.TRENDING,
        limit: Annotated[
            int,
            Field(
                ge=1,
                le=15,
                description="Maximum number of papers to return (1 to 15, default 10).",
            ),
        ] = 10,
    ) -> dict[str, Any]:
        return await scholar_client.discover_papers(
            field=field.value,
            period=period.value,
            sort=sort.value,
            limit=limit,
            huggingface_api_key=_secret(ctx, "huggingface_api_key"),
        )


def _secret(ctx: Context, key: str) -> str | None:
    # 用户秘密配置经 MCP 元数据传递，不能成为模型可见的工具参数。
    value = get_tool_config_value(ctx, key)
    return value.strip() or None if isinstance(value, str) else None


__all__ = [
    "register_scholar_tools",
]

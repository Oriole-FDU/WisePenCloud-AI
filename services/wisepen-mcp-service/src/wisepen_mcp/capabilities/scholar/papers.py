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


class LookupPurpose(StrEnum):
    DETAILS = "details"
    ENRICH = "enrich"
    FULL = "full"


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
            "Search papers through Scholar. The field and source scope specify the requested operation; Scholar "
            "owns query normalization, routing, and capability availability. The response data is returned directly."
        ),
    )
    async def search_papers(
        ctx: Context,
        query: Annotated[
            str,
            Field(
                min_length=1,
                description="Non-empty research question or search phrase.",
            ),
        ],
        field: Annotated[
            SearchField,
            Field(description="Research field used by Scholar for local search."),
        ] = SearchField.GENERAL,
        published_after: Annotated[
            date | None, Field(description="Inclusive publication date lower bound.")
        ] = None,
        published_before: Annotated[
            date | None, Field(description="Inclusive publication date upper bound.")
        ] = None,
        limit: Annotated[
            int, Field(ge=1, le=20, description="Maximum number of papers to return.")
        ] = 10,
        source_scope: Annotated[
            SearchSourceScope,
            Field(description="Requested Scholar source scope."),
        ] = SearchSourceScope.DEFAULT,
    ) -> dict[str, Any]:
        return await scholar_client.search_papers(
            query=query,
            field=field.value,
            published_after=published_after,
            published_before=published_before,
            limit=limit,
            source_scope=source_scope.value,
            openalex_api_key=_openalex_key(ctx),
        )

    @mcp.tool(
        name="lookup_papers",
        description=(
            "Look up 1 to 20 paper identifiers through Scholar. Scholar owns identifier normalization and "
            "determines whether the requested purpose is available. The response data is returned directly."
        ),
    )
    async def lookup_papers(
        ctx: Context,
        identifiers: Annotated[
            list[str],
            Field(
                min_length=1,
                max_length=20,
                description="DOI, arXiv ID, or another Scholar-supported paper identifier.",
            ),
        ],
        purpose: Annotated[
            LookupPurpose,
            Field(description="Requested Scholar lookup purpose."),
        ] = LookupPurpose.DETAILS,
    ) -> dict[str, Any]:
        return await scholar_client.lookup_papers(
            identifiers=identifiers,
            purpose=purpose.value,
            openalex_api_key=_openalex_key(ctx),
        )

    @mcp.tool(
        name="discover_papers",
        description=(
            "Discover papers by field and time window. Scholar returns the actual window and execution result."
        ),
    )
    async def discover_papers(
        field: Annotated[
            DiscoveryField,
            Field(description="A field with a registered discovery capability."),
        ],
        period: Annotated[
            DiscoveryPeriod, Field(description="Daily, natural week, or natural month.")
        ] = DiscoveryPeriod.DAILY,
        sort: Annotated[
            DiscoverySort,
            Field(
                description="Use recent; period-scoped trending is currently unavailable from HF."
            ),
        ] = DiscoverySort.RECENT,
        limit: Annotated[
            int, Field(ge=1, le=15, description="Maximum number of papers to return.")
        ] = 10,
    ) -> dict[str, Any]:
        return await scholar_client.discover_papers(
            field=field.value,
            period=period.value,
            sort=sort.value,
            limit=limit,
        )


def _openalex_key(ctx: Context) -> str | None:
    # 用户秘密配置经 MCP 元数据传递，不能成为模型可见的工具参数。
    value = get_tool_config_value(ctx, "api_key")
    return value.strip() or None if isinstance(value, str) else None


__all__ = [
    "register_scholar_tools",
]

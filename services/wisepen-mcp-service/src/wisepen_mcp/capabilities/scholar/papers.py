from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field

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


def register_scholar_tools(mcp: FastMCP, scholar_client: ScholarClient) -> None:
    @mcp.tool(
        name="search_papers",
        description=(
            "Search papers through Scholar. The field and source scope specify the requested operation; Scholar "
            "owns query normalization, routing, and capability availability. The response data is returned directly."
        ),
    )
    async def search_papers(
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
        )

    @mcp.tool(
        name="lookup_papers",
        description=(
            "Look up 1 to 20 paper identifiers through Scholar. Scholar owns identifier normalization and "
            "determines whether the requested purpose is available. The response data is returned directly."
        ),
    )
    async def lookup_papers(
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
        )


__all__ = [
    "register_scholar_tools",
]

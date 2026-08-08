from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from wisepen_mcp.capabilities.core.tools import get_tool_config_value

from .services import SearchMode, SearchProviderName
from .services.service import WebSearchService

DEFAULT_SEARCH_RESULTS = 10
MAX_SEARCH_RESULTS = 20
TOOL_DESCRIPTION = (
    "Description:\n"
    "Search external information. search_query controls what the provider retrieves, "
    "while ranking_query describes the full information need used to reorder results. "
    "Use academic mode for literature search; providers without native academic "
    "support fall back to web search.\n"
    "Output:\n"
    "Returns relevance-ordered source candidates with URLs and excerpts. Use those "
    "candidates as evidence. supplier_answer, when present, is only a provider summary "
    "and should be checked against the sources. In the final response, every conclusion "
    "supported by a returned URL must cite it with an inline Markdown link in the form "
    "[brief description, usually the official website name](exact URL)."
)

SearchQuery = Annotated[
    str,
    Field(min_length=1, description="Concise keywords sent to the search provider."),
]
RankingQuery = Annotated[
    str,
    Field(
        min_length=1,      
        description="Complete natural-language question used to rank the returned candidates." \
        "such as 'What is the best way to learn Python programming?'",
    ),
]
SearchModeArgument = Annotated[
    SearchMode,
    Field(description="Use academic for literature search; unsupported providers fall back to web."),
]
MaxResults = Annotated[
    int,
    Field(
        ge=1, le=MAX_SEARCH_RESULTS,
        description="Maximum number of search candidates to return.",
    ),
]


def register_web_search_tools(mcp: FastMCP, service: WebSearchService) -> None:
    @mcp.tool(name="platform_search", description=TOOL_DESCRIPTION)
    async def platform_search(
        search_query: SearchQuery,
        ranking_query: RankingQuery,
        ctx: Context,
        mode: SearchModeArgument = SearchMode.WEB,
        max_results: MaxResults = DEFAULT_SEARCH_RESULTS,
    ) -> dict[str, Any]:
        return (
            await service.search(
                provider=None,
                api_key=_tool_api_key(ctx),
                search_query=search_query,
                ranking_query=ranking_query,
                mode=mode,
                max_results=max_results,
            )
        ).model_dump(mode="json", exclude_none=True)


    @mcp.tool(name="exa_search", description=TOOL_DESCRIPTION)
    async def exa_search(
        search_query: SearchQuery,
        ranking_query: RankingQuery,
        ctx: Context,
        mode: SearchModeArgument = SearchMode.WEB,
        max_results: MaxResults = DEFAULT_SEARCH_RESULTS,
    ) -> dict[str, Any]:
        return (
            await service.search(
                provider=SearchProviderName.EXA,
                api_key=_tool_api_key(ctx),
                search_query=search_query,
                ranking_query=ranking_query,
                mode=mode,
                max_results=max_results,
            )
        ).model_dump(mode="json", exclude_none=True)


    @mcp.tool(name="tavily_search", description=TOOL_DESCRIPTION)
    async def tavily_search(
        search_query: SearchQuery,
        ranking_query: RankingQuery,
        ctx: Context,
        mode: SearchModeArgument = SearchMode.WEB,
        max_results: MaxResults = DEFAULT_SEARCH_RESULTS,
    ) -> dict[str, Any]:
        return (
            await service.search(
                provider=SearchProviderName.TAVILY,
                api_key=_tool_api_key(ctx),
                search_query=search_query,
                ranking_query=ranking_query,
                mode=mode,
                max_results=max_results,
            )
        ).model_dump(mode="json", exclude_none=True)


    @mcp.tool(name="anysearch_search", description=TOOL_DESCRIPTION)
    async def anysearch_search(
        search_query: SearchQuery,
        ranking_query: RankingQuery,
        ctx: Context,
        mode: SearchModeArgument = SearchMode.WEB,
        max_results: MaxResults = DEFAULT_SEARCH_RESULTS,
    ) -> dict[str, Any]:
        return (
            await service.search(
                provider=SearchProviderName.ANYSEARCH,
                api_key=_tool_api_key(ctx),
                search_query=search_query,
                ranking_query=ranking_query,
                mode=mode,
                max_results=max_results,
            )
        ).model_dump(mode="json", exclude_none=True)


    @mcp.tool(name="baidu_qianfan_search", description=TOOL_DESCRIPTION)
    async def baidu_qianfan_search(
        search_query: SearchQuery,
        ranking_query: RankingQuery,
        ctx: Context,
        mode: SearchModeArgument = SearchMode.WEB,
        max_results: MaxResults = DEFAULT_SEARCH_RESULTS,
    ) -> dict[str, Any]:
        return (
            await service.search(
                provider=SearchProviderName.BAIDU_QIANFAN,
                api_key=_tool_api_key(ctx),
                search_query=search_query,
                ranking_query=ranking_query,
                mode=mode,
                max_results=max_results,
            )
        ).model_dump(mode="json", exclude_none=True)


    @mcp.tool(name="tinyfish_search", description=TOOL_DESCRIPTION)
    async def tinyfish_search(
        search_query: SearchQuery,
        ranking_query: RankingQuery,
        ctx: Context,
        mode: SearchModeArgument = SearchMode.WEB,
        max_results: MaxResults = DEFAULT_SEARCH_RESULTS,
    ) -> dict[str, Any]:
        return (
            await service.search(
                provider=SearchProviderName.TINYFISH,
                api_key=_tool_api_key(ctx),
                search_query=search_query,
                ranking_query=ranking_query,
                mode=mode,
                max_results=max_results,
            )
        ).model_dump(mode="json", exclude_none=True)


    @mcp.tool(name="firecrawl_search", description=TOOL_DESCRIPTION)
    async def firecrawl_search(
        search_query: SearchQuery,
        ranking_query: RankingQuery,
        ctx: Context,
        mode: SearchModeArgument = SearchMode.WEB,
        max_results: MaxResults = DEFAULT_SEARCH_RESULTS,
    ) -> dict[str, Any]:
        return (
            await service.search(
                provider=SearchProviderName.FIRECRAWL,
                api_key=_tool_api_key(ctx),
                search_query=search_query,
                ranking_query=ranking_query,
                mode=mode,
                max_results=max_results,
            )
        ).model_dump(mode="json", exclude_none=True)


def _tool_api_key(ctx: Context) -> str | None:
    api_key = get_tool_config_value(ctx, "api_key")
    return api_key.strip() if isinstance(api_key, str) and api_key.strip() else None

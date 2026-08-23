import httpx
from mcp.server.fastmcp import FastMCP

from ...core.config.app_settings import settings
from .providers import (
    AnySearchTool,
    BaiduQianfanSearchTool,
    ExaSearchTool,
    FirecrawlSearchTool,
    PlatformSearchTool,
    TavilySearchTool,
    TinyFishSearchTool,
)


def register_web_search_tools(
    mcp: FastMCP,
) -> None:

    web_search_http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.WEB_SEARCH_HTTP_TIMEOUT_SECONDS),
    )

    platform_search_tool = PlatformSearchTool(
        http_client=web_search_http_client,
    )
    exa_search_tool = ExaSearchTool(
        http_client=web_search_http_client,
    )
    tavily_search_tool = TavilySearchTool(
        http_client=web_search_http_client,
    )
    anysearch_search_tool = AnySearchTool(
        http_client=web_search_http_client,
    )
    baidu_qianfan_search_tool = BaiduQianfanSearchTool(
        http_client=web_search_http_client,
    )
    tinyfish_search_tool = TinyFishSearchTool(
        http_client=web_search_http_client,
    )
    firecrawl_search_tool = FirecrawlSearchTool(
        http_client=web_search_http_client,
    )
    web_search_tools = [
        platform_search_tool,
        exa_search_tool,
        tavily_search_tool,
        anysearch_search_tool,
        baidu_qianfan_search_tool,
        tinyfish_search_tool,
        firecrawl_search_tool,
    ]

    for tool in web_search_tools:
        tool.register(mcp)


__all__ = ["register_web_search_tools"]

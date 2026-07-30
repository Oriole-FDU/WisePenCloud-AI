from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP

from wisepen_mcp.capabilities.web_search.services import (
    SearchMode,
    WebSearchToolResult,
)
from wisepen_mcp.capabilities.web_search.services.models import (
    SearchPipelineResult,
    SearchProviderName,
    SearchResponse,
    WebSearchCandidate,
)
from wisepen_mcp.capabilities.web_search.services import WebSearchService
from wisepen_mcp.capabilities.web_search.tools import register_web_search_tools


class _SearchPipeline:
    async def search(self, **kwargs):
        self.kwargs = kwargs
        return SearchPipelineResult(
            search_query=kwargs["search_query"],
            response=SearchResponse(
                query=kwargs["search_query"],
                provider=SearchProviderName.EXA,
                answer="Provider summary",
            ),
            candidates=(
                WebSearchCandidate(
                    candidate_id="[1]",
                    title="WisePen",
                    url="https://example.com/wisepen",
                    snippet="A writing assistant.",
                ),
            ),
        )


class _SourceFactory:
    def build(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace()


@pytest.mark.asyncio
async def test_service_returns_typed_agent_result() -> None:
    pipeline = _SearchPipeline()
    source_factory = _SourceFactory()
    service = WebSearchService(
        search_pipeline=pipeline,
        source_factory=source_factory,
    )

    result = await service.search(
        provider=SearchProviderName.EXA,
        api_key="secret",
        search_query="  wise pen  ",
        ranking_query="  What is WisePen?  ",
        mode=SearchMode.WEB,
        max_results=5,
    )

    assert isinstance(result, WebSearchToolResult)
    assert result.query == "wise pen"
    assert result.mode is SearchMode.WEB
    assert result.candidates[0].candidate_id == "[1]"
    assert result.candidates[0].url == "https://example.com/wisepen"
    assert result.supplier_answer == "Provider summary"
    assert pipeline.kwargs["ranking_query"] == "What is WisePen?"
    assert source_factory.kwargs == {
        "provider": SearchProviderName.EXA,
        "api_key": "secret",
    }


@pytest.mark.asyncio
async def test_registration_exposes_seven_tools_with_dict_output() -> None:
    mcp = FastMCP("test")
    register_web_search_tools(mcp, SimpleNamespace())

    tools = {tool.name: tool for tool in await mcp.list_tools()}

    assert set(tools) == {
        "platform_search",
        "exa_search",
        "tavily_search",
        "anysearch_search",
        "baidu_qianfan_search",
        "tinyfish_search",
        "firecrawl_search",
    }
    platform_search = tools["platform_search"]
    assert platform_search.inputSchema["required"] == [
        "search_query",
        "ranking_query",
    ]
    assert set(platform_search.inputSchema["properties"]) == {
        "search_query",
        "ranking_query",
        "mode",
        "max_results",
    }
    assert platform_search.outputSchema["type"] == "object"

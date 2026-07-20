from __future__ import annotations

from types import SimpleNamespace

import pytest

from chat.application.tools.search_tools.web_search.tools import (
    AnySearchSearchTool,
    BaiduQianfanSearchTool,
    ExaSearchTool,
    PlatformSearchTool,
    TavilySearchTool,
)
from chat.application.tools.search_tools.web_search.services.pipeline import (
    SearchPipelineResult,
    WebSearchCandidate,
)
from chat.application.tools.search_tools.web_search.services.providers.core.models import (
    ProviderSearchResponse,
    SearchMode,
    SearchProviderName,
)
from chat.application.tools.search_tools.web_search.services.providers.core.protocols import (
    ProviderSearcher,
)


class FakeSearchPipeline:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search(self, **kwargs: object) -> SearchPipelineResult:
        self.calls.append(kwargs)
        candidate = WebSearchCandidate(
            candidate_id="[1]",
            title="Attention Is All You Need",
            url="https://arxiv.org/abs/1706.03762",
            snippet="snippet",
            highlights=("highlight",),
        )
        return SearchPipelineResult(
            search_query=str(kwargs["search_query"]),
            response=ProviderSearchResponse(
                query=str(kwargs["search_query"]),
                provider=SearchProviderName.EXA,
                answer="supplier answer",
            ),
            candidates=(candidate,),
        )


class FakeSearchSourceFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def build(
        self,
        *,
        provider: SearchProviderName | None,
        api_key: str | None,
    ) -> object:
        self.calls.append({"provider": provider, "api_key": api_key})
        return SimpleNamespace(provider=provider)


@pytest.mark.asyncio
async def test_provider_academic_search_defaults_to_web() -> None:
    response = ProviderSearchResponse(
        query="rag paper",
        provider=None,
    )

    class WebOnlySearcher(ProviderSearcher):
        async def search_web(
            self,
            *,
            query: str,
            max_results: int,
        ) -> ProviderSearchResponse:
            return response

    result = await WebOnlySearcher().search_academic(
        query="rag paper",
        max_results=10,
    )

    assert result is response


@pytest.mark.parametrize(
    ("tool_type", "name"),
    (
        (PlatformSearchTool, "platform_search"),
        (ExaSearchTool, "exa_search"),
        (TavilySearchTool, "tavily_search"),
        (AnySearchSearchTool, "anysearch_search"),
        (BaiduQianfanSearchTool, "baidu_qianfan_search"),
    ),
)
def test_each_web_search_source_has_a_distinct_tool_definition(
    tool_type: type[object],
    name: str,
) -> None:
    tool = tool_type(
        search_pipeline=FakeSearchPipeline(),
        source_factory=FakeSearchSourceFactory(),
    )

    assert tool.definition.llm_spec.name == name


@pytest.mark.asyncio
async def test_exa_tool_keeps_its_own_identity_and_custom_key() -> None:
    pipeline = FakeSearchPipeline()
    source_factory = FakeSearchSourceFactory()
    tool = ExaSearchTool(search_pipeline=pipeline, source_factory=source_factory)

    result = await tool.execute(
        context={},
        config={"api_key": "custom-key"},
        query={
            "search_query": "rag paper",
            "ranking_query": "Which paper introduced the Transformer architecture?",
        },
        mode="academic",
    )

    assert tool.definition.llm_spec.name == "exa_search"
    assert tool.definition.config_spec is not None
    assert source_factory.calls == [
        {"provider": SearchProviderName.EXA, "api_key": "custom-key"}
    ]
    assert pipeline.calls[0]["mode"] == SearchMode.ACADEMIC
    assert pipeline.calls[0]["search_query"] == "rag paper"
    assert (
        pipeline.calls[0]["ranking_query"]
        == "Which paper introduced the Transformer architecture?"
    )
    assert result["candidates"] == (
        WebSearchCandidate(
            candidate_id="[1]",
            url="https://arxiv.org/abs/1706.03762",
            title="Attention Is All You Need",
            snippet="snippet",
            highlights=("highlight",),
        ),
    )
    assert result["supplier_answer"] == "supplier answer"


@pytest.mark.asyncio
async def test_platform_tool_uses_the_platform_source_without_user_config() -> None:
    pipeline = FakeSearchPipeline()
    source_factory = FakeSearchSourceFactory()
    tool = PlatformSearchTool(search_pipeline=pipeline, source_factory=source_factory)

    result = await tool.execute(
        context={},
        query={
            "search_query": "platform query",
            "ranking_query": "What information is relevant to this platform query?",
        },
    )

    assert tool.definition.llm_spec.name == "platform_search"
    assert tool.definition.config_spec is None
    assert source_factory.calls == [{"provider": None, "api_key": None}]
    assert pipeline.calls[0]["mode"] == SearchMode.WEB
    assert result["mode"] == "web"

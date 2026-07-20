from __future__ import annotations

from types import SimpleNamespace

import pytest

from chat.application.tools.search_tools.web_search.services.models import (
    SearchMode,
    SearchResponse,
    SearchResult,
)
from chat.application.tools.search_tools.web_search.services.pipeline import (
    SearchPipeline,
)
from chat.application.tools.search_tools.web_search.services.providers.base import (
    SearchProviderNetworkError,
)
from chat.application.tools.search_tools.web_search.services.sources import (
    WebSearchSourceScope,
)
from chat.application.utils.ranking import (
    RankQuery,
    RankedCandidate,
    RankingPipeline,
)


class FakeSearcher:
    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
    ) -> SearchResponse:
        return SearchResponse(
            query=query,
            provider=None,
            results=(
                SearchResult(
                    title="First result",
                    url="https://example.com/first",
                    snippet="First snippet",
                    highlights=("First highlight",),
                ),
                SearchResult(
                    title="Second result",
                    url="https://example.com/second",
                ),
            ),
        )


class FailingSearcher:
    async def search_web(
        self,
        *,
        query: str,
        max_results: int,
    ) -> SearchResponse:
        raise SearchProviderNetworkError("network unavailable")


class ReverseReranker:
    query: RankQuery
    ranked: tuple[RankedCandidate, ...]

    async def rerank(
        self,
        *,
        query: RankQuery,
        ranked: tuple[RankedCandidate, ...],
    ) -> tuple[RankedCandidate, ...]:
        self.query = query
        self.ranked = ranked
        return tuple(reversed(ranked))


@pytest.mark.asyncio
async def test_search_pipeline_ranks_provider_results_with_question() -> None:
    reranker = ReverseReranker()
    pipeline = SearchPipeline(
        ranking_pipeline=RankingPipeline(reranker=reranker),
    )

    result = await pipeline.search(
        search_query="transformer paper",
        ranking_query="Which paper introduced the Transformer architecture?",
        max_results=10,
        source=SimpleNamespace(
            provider=None,
            scope=WebSearchSourceScope.PUBLIC,
            searcher=FakeSearcher(),
        ),
        mode=SearchMode.WEB,
    )

    assert reranker.query.text == (
        "Which paper introduced the Transformer architecture?"
    )
    assert reranker.ranked[0].candidate.fields == {
        "title": "First result",
        "snippet": "First snippet",
        "highlights": "First highlight",
    }
    assert reranker.ranked[0].candidate.text == (
        "Title: First result\nSnippet: First snippet\nHighlight: First highlight"
    )
    assert [candidate.title for candidate in result.candidates] == [
        "Second result",
        "First result",
    ]


@pytest.mark.asyncio
async def test_search_pipeline_degrades_public_provider_errors_to_empty_result() -> (
    None
):
    pipeline = SearchPipeline(ranking_pipeline=RankingPipeline())

    result = await pipeline.search(
        search_query="query",
        ranking_query="What information is relevant?",
        max_results=10,
        source=SimpleNamespace(
            provider=None,
            scope=WebSearchSourceScope.PUBLIC,
            searcher=FailingSearcher(),
        ),
        mode=SearchMode.WEB,
    )

    assert result.response is None
    assert result.candidates == ()


@pytest.mark.asyncio
async def test_search_pipeline_preserves_private_provider_errors() -> None:
    pipeline = SearchPipeline(ranking_pipeline=RankingPipeline())

    with pytest.raises(SearchProviderNetworkError):
        await pipeline.search(
            search_query="query",
            ranking_query="What information is relevant?",
            max_results=10,
            source=SimpleNamespace(
                provider=object(),
                scope=WebSearchSourceScope.PRIVATE,
                searcher=FailingSearcher(),
            ),
            mode=SearchMode.WEB,
        )

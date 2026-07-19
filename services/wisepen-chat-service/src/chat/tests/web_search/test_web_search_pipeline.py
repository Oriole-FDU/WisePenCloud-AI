from __future__ import annotations

from types import SimpleNamespace

import pytest

from chat.application.tools.search_tools.web_search.services.pipeline import (
    SearchPipeline,
)
from chat.application.tools.search_tools.web_search.services.providers.core.models import (
    ProviderSearchResponse,
    ProviderSearchResult,
    SearchMode,
    SearchPreview,
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
    ) -> ProviderSearchResponse:
        return ProviderSearchResponse(
            query=query,
            provider=None,
            results=(
                ProviderSearchResult(
                    title="First result",
                    url="https://example.com/first",
                    preview=SearchPreview(
                        snippet="First snippet",
                        highlights=("First highlight",),
                    ),
                ),
                ProviderSearchResult(
                    title="Second result",
                    url="https://example.com/second",
                ),
            ),
        )


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
    assert [candidate.title for candidate in result.candidates] == [
        "Second result",
        "First result",
    ]

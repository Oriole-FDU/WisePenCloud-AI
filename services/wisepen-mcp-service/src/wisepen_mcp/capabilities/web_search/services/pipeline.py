from __future__ import annotations

from wisepen_mcp.utils.ranking import (
    RankCandidate,
    RankQuery,
    RankRequest,
    RankingPipeline,
)

from .models import (
    SearchMode,
    SearchPipelineResult,
    SearchResponse,
    WebSearchCandidate,
)
from .providers.base import ProviderSearcher


class SearchPipeline:
    """执行搜索并按字段相关性和问句重排候选。"""

    def __init__(self, *, ranking_pipeline: RankingPipeline) -> None:
        self._ranking_pipeline = ranking_pipeline

    async def search(
        self,
        *,
        search_query: str,
        ranking_query: str,
        max_results: int,
        searcher: ProviderSearcher,
        mode: SearchMode,
    ) -> SearchPipelineResult:
        response = await self._request_provider_response(
            query=search_query,
            max_results=max_results,
            searcher=searcher,
            mode=mode,
        )

        candidates = tuple(
            WebSearchCandidate(
                candidate_id=f"[{index}]",
                title=item.title,
                url=item.url,
                snippet=item.snippet,
                highlights=item.highlights,
            )
            for index, item in enumerate(response.results, 1)
        )

        ranked = await self._ranking_pipeline.arank(
            RankRequest(
                query=RankQuery(text=ranking_query),
                candidates=tuple(
                    RankCandidate(
                        candidate_id=candidate.candidate_id,
                        text="\n".join(
                            text
                            for text in (
                                (
                                    f"Title: {candidate.title}"
                                    if candidate.title
                                    else ""
                                ),
                                (
                                    f"Snippet: {candidate.snippet}"
                                    if candidate.snippet
                                    else ""
                                ),
                                *(
                                    f"Highlight: {highlight}"
                                    for highlight in candidate.highlights or ()
                                ),
                            )
                            if text
                        ),
                        fields={
                            "title": candidate.title or "",
                            "snippet": candidate.snippet or "",
                            "highlights": "\n".join(candidate.highlights or ()),
                        },
                    )
                    for candidate in candidates
                ),
                top_k=len(candidates),
                candidate_limit=len(candidates),
            )
        )
        candidates_by_id = {
            candidate.candidate_id: candidate for candidate in candidates
        }

        return SearchPipelineResult(
            search_query=search_query,
            response=response,
            candidates=tuple(
                candidates_by_id[item.candidate_id] for item in ranked.ranked
            ),
        )

    async def _request_provider_response(
        self,
        *,
        query: str,
        max_results: int,
        searcher: ProviderSearcher,
        mode: SearchMode,
    ) -> SearchResponse:
        search = (
            searcher.search_academic
            if mode is SearchMode.ACADEMIC
            else searcher.search_web
        )

        return await search(
            query=query,
            max_results=max_results,
        )

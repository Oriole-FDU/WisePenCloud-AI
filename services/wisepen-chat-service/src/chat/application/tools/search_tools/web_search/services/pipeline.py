from __future__ import annotations

from dataclasses import dataclass

from chat.application.utils.ranking import (
    RankCandidate,
    RankQuery,
    RankRequest,
    RankingPipeline,
)

from .errors import (
    WebSearchCustomApiKeyInvalid,
    WebSearchCustomApiKeyMissing,
    WebSearchEmptyResult,
    WebSearchError,
    WebSearchInternalError,
    WebSearchNetworkError,
)
from .providers.core.errors import (
    SearchProviderCredentialError,
    SearchProviderNetworkError,
)
from .providers.core.models import ProviderSearchResponse, SearchMode
from .sources import (
    CustomSearchSource,
    PlatformDefaultSearchSource,
    WebSearchSourceScope,
)


@dataclass(frozen=True, slots=True)
class WebSearchCandidate:
    candidate_id: str
    title: str
    url: str
    overview: str | None = None
    highlights: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchPipelineResult:
    search_query: str
    response: ProviderSearchResponse | None
    candidates: tuple[WebSearchCandidate, ...]


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
        source: PlatformDefaultSearchSource | CustomSearchSource,
        mode: SearchMode,
    ) -> SearchPipelineResult:
        try:
            response = await self._search_provider(
                query=search_query,
                max_results=max_results,
                source=source,
                mode=mode,
            )
        except WebSearchError:
            if source.scope is WebSearchSourceScope.PRIVATE:
                raise

            response = None

        items = response.results if response is not None else ()
        candidates = tuple(
            WebSearchCandidate(
                candidate_id=f"[{index}]",
                title=item.title,
                url=item.url,
                overview=item.preview.overview,
                highlights=item.preview.highlights,
            )
            for index, item in enumerate(items, 1)
        )

        if not candidates:
            raise WebSearchEmptyResult(
                provider=source.provider,
                reason="搜索没有返回结果",
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
                                candidate.title,
                                candidate.overview,
                                *candidate.highlights,
                            )
                            if text
                        ),
                        fields={
                            "title": candidate.title,
                            "overview": candidate.overview or "",
                            "highlights": "\n".join(candidate.highlights),
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

    async def _search_provider(
        self,
        *,
        query: str,
        max_results: int,
        source: PlatformDefaultSearchSource | CustomSearchSource,
        mode: SearchMode,
    ) -> ProviderSearchResponse:
        search = (
            source.searcher.search_academic
            if mode is SearchMode.ACADEMIC
            else source.searcher.search_web
        )

        try:
            response = await search(
                query=query,
                max_results=max_results,
            )

        except SearchProviderCredentialError as exc:
            if "required" in str(exc).lower():
                raise WebSearchCustomApiKeyMissing(
                    provider=source.provider,
                    reason="不存在 API key",
                ) from exc

            raise WebSearchCustomApiKeyInvalid(
                provider=source.provider,
                reason="API key 无效、过期或额度不足",
            ) from exc

        except SearchProviderNetworkError as exc:
            raise WebSearchNetworkError(
                provider=source.provider,
                reason="网络波动或连接失败",
            ) from exc

        except Exception as exc:
            raise WebSearchInternalError(
                provider=source.provider,
                reason="内部服务错误",
            ) from exc

        if not response.results:
            raise WebSearchEmptyResult(
                provider=source.provider,
                reason="搜索源成功响应但没有返回结果",
            )

        return response

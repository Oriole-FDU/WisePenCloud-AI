from __future__ import annotations

from dataclasses import dataclass

from .candidate_selector import select_recommended_ids
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


MAX_RECOMMENDED_CANDIDATES = 5
FALLBACK_CANDIDATES_COUNT = 3


@dataclass(frozen=True, slots=True)
class WebSearchResult:
    query: str
    responses: tuple[ProviderSearchResponse, ...]


@dataclass(frozen=True, slots=True)
class WebSearchCandidate:
    candidate_id: str
    title: str
    url: str
    overview: str | None = None
    highlights: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchPipelineResult:
    search_result: WebSearchResult
    candidates: tuple[WebSearchCandidate, ...]
    recommended_ids: tuple[str, ...]


class SearchPipeline:
    """执行搜索、构建候选并给出后续抓取的优先顺序。"""

    async def search(
        self,
        *,
        query: str,
        max_results: int,
        source: PlatformDefaultSearchSource | CustomSearchSource,
        mode: SearchMode,
    ) -> SearchPipelineResult:
        try:
            result = await self._search_provider(
                query=query,
                max_results=max_results,
                source=source,
                mode=mode,
            )
        except WebSearchError:
            if source.scope is WebSearchSourceScope.PRIVATE:
                raise

            result = WebSearchResult(
                query=query,
                responses=(),
            )

        candidate_list: list[WebSearchCandidate] = []

        for response in result.responses:
            for item in response.results:
                candidate_list.append(
                    WebSearchCandidate(
                        candidate_id=f"[{len(candidate_list) + 1}]",
                        title=item.title,
                        url=item.url,
                        overview=item.preview.overview,
                        highlights=item.preview.highlights,
                    )
                )

        candidates = tuple(candidate_list)

        if not candidates:
            raise WebSearchEmptyResult(
                provider=source.provider,
                reason="搜索没有返回结果",
            )

        recommended_ids = await select_recommended_ids(
            search_query=query,
            candidates=candidates,
            max_recommended_candidates=MAX_RECOMMENDED_CANDIDATES,
            fallback_candidates_count=FALLBACK_CANDIDATES_COUNT,
        )

        return SearchPipelineResult(
            search_result=result,
            candidates=candidates,
            recommended_ids=recommended_ids,
        )

    async def _search_provider(
        self,
        *,
        query: str,
        max_results: int,
        source: PlatformDefaultSearchSource | CustomSearchSource,
        mode: SearchMode,
    ) -> WebSearchResult:
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

        return WebSearchResult(
            query=query,
            responses=(response,),
        )

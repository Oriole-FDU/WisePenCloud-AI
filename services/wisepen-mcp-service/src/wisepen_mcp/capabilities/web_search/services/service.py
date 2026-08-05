from __future__ import annotations

from common.core.exceptions import ServiceException

from wisepen_mcp.domain.error_codes import McpErrorCode

from .models import (
    SearchMode,
    SearchProviderName,
    WebSearchCandidateResult,
    WebSearchToolResult,
)
from .pipeline import SearchPipeline
from .providers.base import (
    SearchProviderCredentialError,
    SearchProviderError,
    SearchProviderNetworkError,
)
from .sources import SearchSourceFactory


class WebSearchService:
    __slots__ = ("_search_pipeline", "_source_factory")

    def __init__(
        self,
        *,
        search_pipeline: SearchPipeline,
        source_factory: SearchSourceFactory,
    ) -> None:
        self._search_pipeline = search_pipeline
        self._source_factory = source_factory

    async def search(
        self,
        *,
        provider: SearchProviderName | None,
        api_key: str | None,
        search_query: str,
        ranking_query: str,
        mode: SearchMode,
        max_results: int,
    ) -> WebSearchToolResult:
        search_query = search_query.strip()
        ranking_query = ranking_query.strip()
        if not search_query or not ranking_query:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_INVALID,
                "search_query and ranking_query must not be blank.",
            )
        if provider is not None and not api_key:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_CONFIG_MISSING,
                f"{provider.value} API key is not configured.",
            )

        try:
            searcher = self._source_factory.build(
                provider=provider,
                api_key=api_key,
            )
            result = await self._search_pipeline.search(
                search_query=search_query,
                ranking_query=ranking_query,
                max_results=max_results,
                searcher=searcher,
                mode=mode,
            )
        except SearchProviderCredentialError as error:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_CREDENTIAL_INVALID,
                str(error),
            ) from error
        except SearchProviderNetworkError as error:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_UNAVAILABLE,
                str(error),
            ) from error
        except SearchProviderError as error:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_FAILED,
                str(error),
            ) from error

        if not result.candidates:
            raise ServiceException(
                McpErrorCode.WEB_SEARCH_EMPTY_RESULT,
                "The search provider returned no results.",
            )

        return WebSearchToolResult(
            query=result.search_query,
            mode=mode,
            candidates=tuple(
                WebSearchCandidateResult(
                    candidate_id=candidate.candidate_id,
                    title=candidate.title,
                    url=candidate.url,
                    snippet=candidate.snippet,
                    highlights=candidate.highlights,
                )
                for candidate in result.candidates
            ),
            supplier_answer=result.response.answer,
        )

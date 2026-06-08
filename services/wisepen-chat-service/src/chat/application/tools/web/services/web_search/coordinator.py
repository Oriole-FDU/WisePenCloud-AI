import asyncio
import inspect
import uuid
from typing import Any, Callable, List

from chat.application.algorithms.hash import stable_hash
from chat.application.tools.web.services.web_search.enums import ProviderMode
from chat.application.tools.web.services.web_search.errors import (
    CustomSearchProviderUnavailableError,
    EmptySearchResultError,
)
from chat.application.tools.web.services.web_search.models import (
    SearchManyRequest,
    SearchManyResult,
    SearchResponse,
    SearchResult,
    WikipediaGroundingResult,
)
from chat.application.tools.web.services.web_search.runtime.models import VariantSearchResponse, \
    RankedSearchResultCandidate
from chat.application.tools.web.services.web_search.runtime.provider_routing import (
    ProviderCallSelector,
)
from chat.application.tools.web.services.web_search.runtime.query_planning import (
    MERGED_CANDIDATE_LIMIT,
    build_search_plan,
)
from chat.application.tools.web.services.web_search.runtime.result_ranking import (
    SearchResultRankingPipeline,
)
from chat.application.tools.web.services.web_search.runtime.runner import (
    CustomProviderRunner,
)
from chat.application.tools.web.services.web_search.runtime.runner import (
    FourGetSearchRunner,
)
from chat.application.tools.web.services.web_search.runtime.runner.serper import (
    SerperSearchRunner,
)
from chat.application.tools.web.services.web_search.runtime.runner.wikipedia import (
    WikipediaRunner,
)
from common.logger import log_event, log_fail


def _source_to_string(source: object) -> str:
    """将搜索源统一转换为字符串。

    - SearcherName / StrEnum: 使用 value。
    - str: 原样返回。
    - 其他对象: 使用 str。
    """
    value = getattr(source, "value", None)
    if isinstance(value, str):
        return value

    return str(source)


async def _resolve_runner(value: Any) -> CustomProviderRunner:
    """兼容 Dependency Injector 同步 / 异步 provider 返回值。"""
    if inspect.isawaitable(value):
        return await value

    return value


class SearchCoordinator:
    """全网多渠道联网搜索核心调度。"""

    def __init__(
            self,
            *,
            fourget_runner: FourGetSearchRunner,
            serper_runner: SerperSearchRunner,
            wikipedia_runner: WikipediaRunner,
            custom_runner_factory: Callable[..., CustomProviderRunner],
    ) -> None:
        """初始化对象依赖。"""
        self._fourget_runner = fourget_runner
        self._serper_runner = serper_runner
        self._wikipedia_runner = wikipedia_runner
        self._custom_runner_factory = custom_runner_factory

    async def search_many(self, request: SearchManyRequest) -> SearchManyResult:
        is_custom_mode = request.provider_mode == ProviderMode.CUSTOM

        plan = build_search_plan(
            mode=request.mode,
            queries=request.queries,
            wikipedia_keywords=request.wikipedia_keywords,
        )

        log_event(
            "web_search plan built",
            provider_mode=request.provider_mode.value,
            mode=request.mode.value,
            queries=request.queries,
            variant_count=len(plan.query_variants),
            variants=[
                {
                    "text": v.text,
                    "role": v.role.value,
                    "max_results": v.max_results,
                }
                for v in plan.query_variants
            ],
        )

        search_call_id = (
            f"default:{uuid.uuid4().hex[:12]}"
            if not is_custom_mode
            else f"custom_route:{stable_hash(request.user_id)[:8]}"
        )

        groundings_task = None
        if plan.wikipedia_keywords:
            groundings_task = asyncio.create_task(
                self._wikipedia_runner.run_keywords(
                    search_call_id=search_call_id,
                    keywords=plan.wikipedia_keywords,
                )
            )

        collected_responses: List[VariantSearchResponse] = []
        selector = ProviderCallSelector(
            mode=request.mode,
            variants=plan.query_variants,
        )

        if not is_custom_mode:
            platform_variant_results: List[VariantSearchResponse] = []

            default_calls = selector.default_calls()
            if default_calls:
                platform_variant_results = await self._fourget_runner.run_variants(
                    search_call_id=search_call_id,
                    variants=[call.variant for call in default_calls],
                )
                collected_responses.extend(platform_variant_results)

            supplement_calls = selector.supplement_calls(
                primary_responses=platform_variant_results,
            )
            if supplement_calls:
                serper_variant_results = await self._serper_runner.run_variants(
                    search_call_id=search_call_id,
                    variants=[call.variant for call in supplement_calls],
                )
                collected_responses.extend(serper_variant_results)

        else:
            credential = request.custom_provider_credential
            if credential is None:
                raise CustomSearchProviderUnavailableError(
                    provider="custom",
                    message="Custom provider credential is missing from SearchManyRequest.",
                )

            custom_provider_calls = selector.custom_calls(credential=credential)
            if not custom_provider_calls:
                raise CustomSearchProviderUnavailableError(
                    provider=credential.provider,
                    message=(
                        "No custom provider calls generated for "
                        f"{credential.provider.value}."
                    ),
                )

            try:
                custom_runner_value = self._custom_runner_factory(
                    credential=credential,
                    user_id=request.user_id,
                )
                custom_runner = await _resolve_runner(custom_runner_value)

                custom_results = await custom_runner.run(
                    provider_calls=custom_provider_calls,
                )
                collected_responses.extend(custom_results)

            except CustomSearchProviderUnavailableError:
                raise

            except Exception as e:
                raise CustomSearchProviderUnavailableError(
                    provider=credential.provider,
                    message=f"Custom provider search failed: {repr(e)}",
                ) from e

        if not collected_responses:
            if groundings_task is not None:
                groundings_task.cancel()
                try:
                    await groundings_task
                except asyncio.CancelledError:
                    pass

            log_fail(
                "web_search collected_responses empty",
                "no provider returned usable VariantSearchResponse",
                provider_mode=request.provider_mode.value,
                mode=request.mode.value,
                queries=request.queries,
                variant_count=len(plan.query_variants),
            )

            raise EmptySearchResultError(provider="all", queries=request.queries)

        groundings: List[WikipediaGroundingResult] = []
        if groundings_task is not None:
            groundings = await groundings_task

        merged_limit = MERGED_CANDIDATE_LIMIT[request.mode]
        search_result_ranking_pipeline = SearchResultRankingPipeline(
            mode=request.mode,
            merged_limit=merged_limit,
            max_urls_per_domain=2,
        )

        ranked_candidates: List[RankedSearchResultCandidate] = (
            search_result_ranking_pipeline.run(
                variant_responses=collected_responses,
            )
        )

        sources = [
            _source_to_string(vr.response.source)
            for vr in collected_responses
            if vr.response.source
        ]
        combined_source = " | ".join(sources) if sources else ""

        ranked_results = [
            SearchResult(
                title=item.candidate.title,
                url=item.candidate.url,
                snippet=item.candidate.snippet,
            )
            for item in ranked_candidates
        ]

        merged_response = SearchResponse(
            query=" | ".join(request.queries),
            results=ranked_results,
            source=combined_source,
        )

        return SearchManyResult(response=merged_response, groundings=groundings)
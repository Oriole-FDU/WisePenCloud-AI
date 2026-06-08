import asyncio
from abc import ABC, abstractmethod
from typing import List, Optional, Sequence

from chat.application.algorithms.hash import stable_hash
from chat.application.tools.web.services.web_search.cache import (
    SearchCache,
    make_search_cache_descriptor,
)
from chat.application.tools.web.services.web_search.enums import (
    ProviderMode,
    SearchPurpose,
    SearcherName,
)
from chat.application.tools.web.services.web_search.runtime.models import VariantSearchResponse, QueryVariant
from chat.application.tools.web.services.web_search.searcher.base import WebSearcher
from chat.application.tools.web.services.web_search.utils.results import (
    has_response_content,
)
from common.logger import log_event, log_fail


class SearchRunner(ABC):
    """检索执行器顶层接口。

    - 定义多变体并发调度契约。
    - 所有搜索 runner 实现此接口。
    """

    @abstractmethod
    async def run_variants(
        self,
        *,
        search_call_id: str,
        variants: Sequence[QueryVariant],
    ) -> List[VariantSearchResponse]:
        """并发调度多条检索变体。

        Args:
        - search_call_id: 单次搜索调用追踪 ID。
        - variants: 待执行的检索变体列表。

        Returns:
        - 成功返回的 VariantSearchResponse 列表。
        """
        ...


class BaseSearchRunner(SearchRunner):
    """检索执行器通用模板。

    - 封装缓存读取。
    - 封装多变体并发执行。
    - 封装异常隔离与可观测日志。
    - 子类只需要提供 searcher 实例。
    """

    def __init__(
        self,
        *,
        searcher: WebSearcher,
        cache: SearchCache,
        purpose: SearchPurpose = SearchPurpose.RECALL,
        provider_mode: ProviderMode = ProviderMode.DEFAULT,
        user_id: Optional[str] = None,
    ) -> None:
        self._searcher = searcher
        self._cache = cache
        self._purpose = purpose
        self._provider_mode = provider_mode
        self._user_id = user_id
        self._user_id_hash = stable_hash(user_id) if user_id else None

    async def run_variants(
        self,
        *,
        search_call_id: str,
        variants: Sequence[QueryVariant],
    ) -> List[VariantSearchResponse]:
        """并发调度所有变体，并记录被过滤的异常/空结果。

        Args:
        - search_call_id: 单次搜索调用追踪 ID。
        - variants: 待执行的检索变体列表。

        Returns:
        - 成功返回的 VariantSearchResponse 列表。
        """
        provider_name = self._searcher.name

        if not variants:
            log_fail(
                "web_search runner",
                "run_variants received empty variants",
                provider=provider_name.value,
                provider_mode=self._provider_mode.value,
                search_call_id=search_call_id,
            )
            return []

        log_event(
            "web_search runner run_variants started",
            provider=provider_name.value,
            provider_mode=self._provider_mode.value,
            search_call_id=search_call_id,
            variant_count=len(variants),
            variants=[
                {
                    "query": variant.text,
                    "role": variant.role.value,
                    "max_results": variant.max_results,
                }
                for variant in variants
            ],
        )

        raw_results = await asyncio.gather(
            *(
                self._run_one_variant(
                    variant=variant,
                    search_call_id=search_call_id,
                )
                for variant in variants
            ),
            return_exceptions=True,
        )

        results: List[VariantSearchResponse] = []

        for variant, item in zip(variants, raw_results):
            if isinstance(item, Exception):
                log_fail(
                    "web_search runner variant task crashed",
                    repr(item),
                    provider=provider_name.value,
                    provider_mode=self._provider_mode.value,
                    search_call_id=search_call_id,
                    query=variant.text,
                    role=variant.role.value,
                    max_results=variant.max_results,
                )
                continue

            if item is None:
                log_fail(
                    "web_search runner variant returned none",
                    "variant result is None",
                    provider=provider_name.value,
                    provider_mode=self._provider_mode.value,
                    search_call_id=search_call_id,
                    query=variant.text,
                    role=variant.role.value,
                    max_results=variant.max_results,
                )
                continue

            if not isinstance(item, VariantSearchResponse):
                log_fail(
                    "web_search runner variant returned unexpected type",
                    type(item).__name__,
                    provider=provider_name.value,
                    provider_mode=self._provider_mode.value,
                    search_call_id=search_call_id,
                    query=variant.text,
                    role=variant.role.value,
                    max_results=variant.max_results,
                )
                continue

            results.append(item)

        log_event(
            "web_search runner run_variants finished",
            provider=provider_name.value,
            provider_mode=self._provider_mode.value,
            search_call_id=search_call_id,
            variant_count=len(variants),
            response_count=len(results),
            result_items=sum(len(item.response.results) for item in results),
        )

        return results

    async def _run_one_variant(
        self,
        variant: QueryVariant,
        *,
        search_call_id: str,
    ) -> Optional[VariantSearchResponse]:
        """执行单条检索变体。

        Args:
        - variant: 单条检索变体。
        - search_call_id: 搜索调用追踪 ID。

        Returns:
        - 缓存命中或搜索成功时返回 VariantSearchResponse。
        - 异常、空结果或无可用内容时返回 None。
        """
        provider_name: SearcherName = self._searcher.name

        log_event(
            "web_search variant started",
            provider=provider_name.value,
            provider_mode=self._provider_mode.value,
            search_call_id=search_call_id,
            query=variant.text,
            role=variant.role.value,
            max_results=variant.max_results,
        )

        try:
            desc = make_search_cache_descriptor(
                source=provider_name,
                query=variant.text,
                max_results=variant.max_results,
                purpose=self._purpose,
                provider_mode=self._provider_mode,
                user_id=self._user_id,
            )

            cached = self._cache.get(desc)

        except Exception as e:
            log_fail(
                "web_search variant preflight failed",
                repr(e),
                provider=provider_name.value,
                provider_mode=self._provider_mode.value,
                search_call_id=search_call_id,
                query=variant.text,
                role=variant.role.value,
                max_results=variant.max_results,
            )
            return None

        if cached is not None:
            result_count = len(cached.response.results)

            log_event(
                "web_search variant cache hit",
                provider=provider_name.value,
                provider_mode=self._provider_mode.value,
                search_call_id=search_call_id,
                query=variant.text,
                role=variant.role.value,
                max_results=variant.max_results,
                result_count=result_count,
            )

            if not has_response_content(cached.response):
                log_fail(
                    "web_search cached response rejected",
                    "has_response_content=false",
                    provider=provider_name.value,
                    provider_mode=self._provider_mode.value,
                    search_call_id=search_call_id,
                    query=variant.text,
                    role=variant.role.value,
                    max_results=variant.max_results,
                    result_count=result_count,
                )
                return None

            return VariantSearchResponse(
                variant=variant,
                response=cached.response,
                cache_hit=True,
            )

        try:
            log_event(
                "web_search variant calling searcher",
                provider=provider_name.value,
                provider_mode=self._provider_mode.value,
                search_call_id=search_call_id,
                query=variant.text,
                role=variant.role.value,
                max_results=variant.max_results,
            )

            response = await self._searcher.search(
                query=variant.text,
                max_results=variant.max_results,
            )

        except Exception as e:
            log_fail(
                "web_search variant search failed",
                repr(e),
                provider=provider_name.value,
                provider_mode=self._provider_mode.value,
                search_call_id=search_call_id,
                query=variant.text,
                role=variant.role.value,
                max_results=variant.max_results,
            )
            return None

        log_event(
            "web_search variant search returned",
            provider=provider_name.value,
            provider_mode=self._provider_mode.value,
            search_call_id=search_call_id,
            query=variant.text,
            role=variant.role.value,
            max_results=variant.max_results,
            result_count=len(response.results),
            source=(
                response.source.value
                if hasattr(response.source, "value")
                else str(response.source)
            ),
        )

        if not has_response_content(response):
            log_fail(
                "web_search variant response rejected",
                "has_response_content=false",
                provider=provider_name.value,
                provider_mode=self._provider_mode.value,
                search_call_id=search_call_id,
                query=variant.text,
                role=variant.role.value,
                max_results=variant.max_results,
                result_count=len(response.results),
            )
            return None

        try:
            self._cache.set(desc, response)
        except Exception as e:
            log_fail(
                "web_search variant cache write failed",
                repr(e),
                provider=provider_name.value,
                provider_mode=self._provider_mode.value,
                search_call_id=search_call_id,
                query=variant.text,
                role=variant.role.value,
                max_results=variant.max_results,
                result_count=len(response.results),
            )

        return VariantSearchResponse(
            variant=variant,
            response=response,
            cache_hit=False,
        )
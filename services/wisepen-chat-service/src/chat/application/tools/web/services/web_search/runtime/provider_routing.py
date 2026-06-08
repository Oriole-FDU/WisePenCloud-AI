from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from chat.application.tools.web.services.web_search.enums import (
    QueryRole,
    SearchMode,
    SearcherName,
)
from chat.application.tools.web.services.web_search.provider_policy.models import CustomProviderCredential
from chat.application.tools.web.services.web_search.runtime.models import VariantSearchResponse, QueryVariant
from chat.application.tools.web.utils.domains import extract_domain

# 刚性质量大闸：仅针对大模型选择的 DEEP 模式生效
_DEEP_MIN_USEFUL_RESULTS = 5
_DEEP_MIN_UNIQUE_DOMAINS = 3


@dataclass(frozen=True, slots=True)
class ProviderCall:
    """封装一次搜索引擎调用所需的 provider、查询变体和结果数量上限。"""

    provider: SearcherName
    variant: QueryVariant
    max_results: int


class ProviderCallSelector:
    """根据搜索模式和查询变体，决策调用哪些搜索引擎及补充策略。"""

    def __init__(
        self,
        *,
        mode: SearchMode,
        variants: List[QueryVariant],
    ) -> None:
        """初始化 ProviderCallSelector。

        Args:
            mode: 搜索模式（FAST / NORMAL / DEEP）。
            variants: 当前搜索的查询变体列表。
        """
        self._mode = mode
        self._variants = variants
        self._primary_provider = SearcherName.FOURGET

    def default_calls(self) -> List[ProviderCall]:
        """生成默认主力搜索引擎（FOURGET）的调用计划。

        每个查询变体对应一个 ProviderCall。

        Returns:
            默认搜索引擎的调用计划列表。
        """
        return [
            ProviderCall(
                provider=self._primary_provider,
                variant=v,
                max_results=v.max_results,
            )
            for v in self._variants
        ]

    def supplement_calls(
        self,
        primary_responses: Sequence[VariantSearchResponse],
    ) -> List[ProviderCall]:
        """根据主力引擎的结果质量，决策是否需要 SERPER 补充召回。

        - FAST/NORMAL 模式：主力结果为空时补充。
        - DEEP 模式：结果数不足或域名多样性不足时补充。

        Args:
            primary_responses: 主力引擎的搜索结果列表。

        Returns:
            需补充的 SERPER 调用计划，无需补充时返回空列表。
        """
        primary = next((v for v in self._variants if v.role == QueryRole.PRIMARY), None)
        if primary is None:
            return []

        serper_call = [ProviderCall(SearcherName.SERPER, primary, primary.max_results)]

        useful_results = sum(
            1
            for item in primary_responses
            for result in item.response.results
            if result.title.strip() and result.url.strip()
        )

        if self._mode in (SearchMode.FAST, SearchMode.NORMAL):
            return serper_call if useful_results == 0 else []

        if self._mode == SearchMode.DEEP:
            if useful_results < _DEEP_MIN_USEFUL_RESULTS:
                return serper_call

            unique_domains = {
                domain
                for item in primary_responses
                for result in item.response.results
                if (domain := extract_domain(result.url))
            }
            if len(unique_domains) < _DEEP_MIN_UNIQUE_DOMAINS:
                return serper_call

        return []

    def custom_calls(self, credential: CustomProviderCredential) -> List[ProviderCall]:
        """为用户自定义搜索引擎生成调用计划。

        如果凭证中的 provider 是内置源（FOURGET/SERPER）则跳过，
        否则为每个查询变体生成一条调用。

        Args:
            credential: 用户自定义搜索引擎凭证。

        Returns:
            自定义搜索引擎的调用计划列表。
        """
        if credential.provider in (SearcherName.FOURGET, SearcherName.SERPER):
            return []

        return [
            ProviderCall(
                provider=credential.provider,
                variant=v,
                max_results=v.max_results,
            )
            for v in self._variants
        ]

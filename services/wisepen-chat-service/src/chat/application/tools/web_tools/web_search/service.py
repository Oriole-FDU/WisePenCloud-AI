from __future__ import annotations

from dataclasses import dataclass

from .errors import (
    WebSearchCustomApiKeyInvalid,
    WebSearchCustomApiKeyMissing,
    WebSearchCustomError,
    WebSearchEmptyResult,
    WebSearchInternalError,
    WebSearchNetworkError,
)
from .providers.models import ProviderSearchResponse, SearchProviderName
from .routing.endpoint_planner import resolve_endpoint_plans
from .routing.router import WebSearchRouter
from .searcher import WebSearchProviderSearcher
from .searchers import (
    SearchProviderCredentialError,
    SearchProviderError,
    SearchProviderNetworkError,
)


@dataclass(frozen=True, slots=True)
class WebSearchCustomSource:
    """单次 custom 搜索源。

    searcher 必须由调用方按用户凭证提前构造好，service 不读取用户凭证，
    也不把 custom source 合并进平台默认源。
    """

    provider: SearchProviderName  # 用户自定义搜索源类型
    searcher: WebSearchProviderSearcher  # 已按用户凭证和 source_id 构造好的 searcher
    api_key: str = ""  # 原始 custom key，仅用于进入 search 前做缺失判断，不落库


@dataclass(frozen=True, slots=True)
class WebSearchResult:
    """Web search service 的轻量返回。"""

    query: str  # 本次查询文本
    responses: tuple[ProviderSearchResponse, ...]  # 成功 provider 的归一化响应


class WebSearchService:
    """Web search 编排服务。

    当前策略：
    - 平台默认只使用 4get。
    - 平台 Exa 暂不进入链路。
    - custom 请求跳过 4get，只使用调用方提供的自定义搜索源。
    """

    def __init__(
            self,
            *,
            platform_searcher: WebSearchProviderSearcher,
            router: WebSearchRouter | None = None,
    ) -> None:
        self._platform_searcher = platform_searcher
        self._router = router or WebSearchRouter()

    async def search(
            self,
            *,
            query: str,
            max_results: int = 10,
            custom_source: WebSearchCustomSource | None = None,
    ) -> WebSearchResult:
        route = await self._router.route(query)

        if custom_source is not None and not custom_source.api_key.strip():
            raise WebSearchCustomApiKeyMissing(
                provider=custom_source.provider,
                reason="不存在 api key",
            )

        # 平台只走默认源，custom 走调用方传入的源。
        providers = (
            [custom_source.provider]
            if custom_source is not None
            else [SearchProviderName.FOURGET]
        )
        searcher = custom_source.searcher if custom_source is not None else self._platform_searcher

        responses: list[ProviderSearchResponse] = []

        for plan in resolve_endpoint_plans(route, providers):
            try:
                response = await searcher.search(
                    query=query,
                    plan=plan,
                    max_results=max_results,
                )

                if custom_source is not None and not response.results:
                    raise WebSearchEmptyResult(
                        provider=plan.provider,
                        reason="搜索源成功响应但没有返回结果",
                    )
                responses.append(response)

            except SearchProviderCredentialError as exc:
                if custom_source is None:
                    continue
                raise _to_custom_credential_error(plan.provider, exc) from exc

            except SearchProviderNetworkError as exc:
                if custom_source is None:
                    continue
                raise WebSearchNetworkError(
                    provider=plan.provider,
                    reason="网络波动或连接失败",
                ) from exc

            except SearchProviderError as exc:
                if custom_source is None:
                    continue
                raise WebSearchInternalError(
                    provider=plan.provider,
                    reason="内部服务错误",
                ) from exc

            except (WebSearchCustomError, WebSearchEmptyResult, WebSearchNetworkError, WebSearchInternalError):
                raise

            except Exception as exc:
                if custom_source is None:
                    continue
                raise WebSearchInternalError(
                    provider=plan.provider,
                    reason="内部服务错误",
                ) from exc

        return WebSearchResult(
            query=query,
            responses=tuple(responses),
        )


def _to_custom_credential_error(
        provider: SearchProviderName,
        exc: SearchProviderCredentialError,
) -> WebSearchCustomError:
    """将底层 provider 抛出的原生凭证异常，映射为面向用户的业务层异常。"""
    text = str(exc).lower()
    if "required" in text or "api key is required" in text:
        return WebSearchCustomApiKeyMissing(
            provider=provider,
            reason="不存在 api key",
        )
    return WebSearchCustomApiKeyInvalid(
        provider=provider,
        reason="api key 失效、过期或者额度耗尽",
    )

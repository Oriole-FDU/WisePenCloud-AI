from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from chat.application.tools.search_tools.web_search.core.errors import (
    WebSearchCustomApiKeyInvalid,
    WebSearchCustomApiKeyMissing,
    WebSearchCustomError,
    WebSearchEmptyResult,
    WebSearchInternalError,
    WebSearchNetworkError,
)
from chat.application.tools.search_tools.web_search.core.sources import (
    WebSearchRuntimeSource,
    WebSearchSourceKind,
)
from chat.application.tools.search_tools.web_search.providers.models import ProviderSearchResponse, SearchProviderName
from chat.application.tools.search_tools.web_search.searchers import (
    ProviderSearcher,
    SearchProviderError,
    SearchProviderCredentialError,
    SearchProviderNetworkError,
)
from common.logger import warn


@dataclass(frozen=True, slots=True)
class WebSearchResult:
    """Web search service 的轻量返回。"""

    query: str
    responses: tuple[ProviderSearchResponse, ...]


async def execute_provider_search(
        *,
        query: str,
        source: WebSearchRuntimeSource,
        search_once: Callable[
            [ProviderSearcher],
            Awaitable[ProviderSearchResponse],
        ],
) -> WebSearchResult:
    """执行单次 provider 搜索，并统一翻译平台源和 custom 源异常语义。"""
    if source.kind == WebSearchSourceKind.CUSTOM and not source.api_key.strip():
        raise WebSearchCustomApiKeyMissing(
            provider=source.provider,
            reason="不存在 api key",
        )

    try:
        response = await search_once(source.searcher)
        if not response.results:
            raise WebSearchEmptyResult(
                provider=source.provider,
                reason="搜索源成功响应但没有返回结果",
            )
        return WebSearchResult(query=query, responses=(response,))

    except SearchProviderCredentialError as exc:
        if source.kind != WebSearchSourceKind.CUSTOM:
            warn(
                "search provider skipped.",
                provider=source.provider,
                source_kind=source.kind,
                reason=exc.__class__.__name__,
            )
            return WebSearchResult(query=query, responses=())
        raise _to_custom_credential_error(source.provider, exc) from exc

    except SearchProviderNetworkError as exc:
        if source.kind != WebSearchSourceKind.CUSTOM:
            warn(
                "search provider skipped.",
                provider=source.provider,
                source_kind=source.kind,
                reason=exc.__class__.__name__,
            )
            return WebSearchResult(query=query, responses=())
        raise WebSearchNetworkError(
            provider=source.provider,
            reason="网络波动或连接失败",
        ) from exc

    except SearchProviderError as exc:
        if source.kind != WebSearchSourceKind.CUSTOM:
            warn(
                "search provider skipped.",
                provider=source.provider,
                source_kind=source.kind,
                reason=exc.__class__.__name__,
            )
            return WebSearchResult(query=query, responses=())
        raise WebSearchInternalError(
            provider=source.provider,
            reason="内部服务错误",
        ) from exc

    except (WebSearchCustomError, WebSearchEmptyResult, WebSearchNetworkError, WebSearchInternalError):
        raise

    except Exception as exc:
        if source.kind != WebSearchSourceKind.CUSTOM:
            warn(
                "search provider skipped.",
                provider=source.provider,
                source_kind=source.kind,
                reason=exc.__class__.__name__,
            )
            return WebSearchResult(query=query, responses=())
        raise WebSearchInternalError(
            provider=source.provider,
            reason="内部服务错误",
        ) from exc


def _to_custom_credential_error(
        provider: SearchProviderName | None,
        exc: SearchProviderCredentialError,
) -> WebSearchCustomError:
    """将底层 provider 抛出的原生凭证异常映射为用户可理解的异常。"""
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

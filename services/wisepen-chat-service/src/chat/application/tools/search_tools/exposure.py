from __future__ import annotations

from typing import Any, Protocol

from chat.application.tools.search_tools.web_search.providers.models import SearchProviderName

PLATFORM_SEARCH_TOOL_NAMES = frozenset({"platform_search"})
SEARCH_TOOL_NAME_BY_PROVIDER = {
    SearchProviderName.EXA: "exa_search",
    SearchProviderName.TAVILY: "tavily_search",
    SearchProviderName.ANYSEARCH: "anysearch_search",
    SearchProviderName.BAIDU_QIANFAN: "baidu_qianfan_search",
}


class WebSearchCredentialExposureRepository(Protocol):
    async def get_active_custom_credential(self, *, user_id: str) -> Any | None: ...

    async def get_platform_credential(self, *, user_id: str) -> Any | None: ...


async def active_search_tool_names(
        *,
        user_id: str,
        credential_repository: WebSearchCredentialExposureRepository,
) -> set[str]:
    """按当前 active 搜索凭证决定本轮可见搜索工具。"""
    active_custom = await credential_repository.get_active_custom_credential(user_id=user_id)
    if active_custom is not None:
        tool_name = SEARCH_TOOL_NAME_BY_PROVIDER.get(active_custom.provider)
        return {tool_name} if tool_name is not None else set()

    active_platform = await credential_repository.get_platform_credential(user_id=user_id)
    if active_platform is not None:
        return set(PLATFORM_SEARCH_TOOL_NAMES)

    return set()

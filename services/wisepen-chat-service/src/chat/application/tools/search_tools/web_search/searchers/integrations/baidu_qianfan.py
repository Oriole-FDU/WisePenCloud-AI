from __future__ import annotations

import httpx

from chat.application.tools.search_tools.web_search.providers.baidu_qianfan import (
    BaiduQianfanSearchRequest,
    map_baidu_qianfan_response,
)
from chat.application.tools.search_tools.web_search.providers.models import SearchProviderName
from chat.application.tools.search_tools.web_search.searchers.base import (
    BaseProviderSearcher,
    SearchProviderConfig,
    SearchProviderCredentialError,
)


class BaiduQianfanSearcher(BaseProviderSearcher):
    provider = SearchProviderName.BAIDU_QIANFAN
    request_class = BaiduQianfanSearchRequest
    response_mapper = staticmethod(map_baidu_qianfan_response)

    def __init__(
            self,
            *,
            http_client: httpx.AsyncClient,
            config: SearchProviderConfig,
    ) -> None:
        if not config.api_key:
            raise SearchProviderCredentialError("Baidu Qianfan API key is required.")
        headers = {
            "X-Appbuilder-Authorization": f"Bearer {config.api_key}",
        }
        super().__init__(http_client=http_client, config=config, headers=headers)

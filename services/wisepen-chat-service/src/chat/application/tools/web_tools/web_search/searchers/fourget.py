from __future__ import annotations

import httpx

from .base import BaseProviderSearcher, SearchProviderConfig
from ..providers.fourget import FourGetSearchRequest, map_fourget_response
from ..providers.models import SearchProviderName


class FourGetSearcher(BaseProviderSearcher):
    provider = SearchProviderName.FOURGET
    request_class = FourGetSearchRequest
    response_mapper = staticmethod(map_fourget_response)

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        config: SearchProviderConfig,
    ) -> None:
        super().__init__(http_client=http_client, config=config)

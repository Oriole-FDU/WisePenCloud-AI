from __future__ import annotations

from chat.application.tools.search_tools.provider_search_tool import ProviderSearchTool
from chat.application.tools.search_tools.web_search.providers.models import SearchProviderName


class AnySearchSearchTool(ProviderSearchTool):
    def __init__(self, **kwargs):
        super().__init__(tool_name="anysearch_search", provider=SearchProviderName.ANYSEARCH, **kwargs)


from __future__ import annotations

from chat.application.tools.search_tools.provider_search_tool import ProviderSearchTool
from chat.application.tools.search_tools.web_search.providers.models import SearchProviderName


class TavilySearchTool(ProviderSearchTool):
    def __init__(self, **kwargs):
        super().__init__(tool_name="tavily_search", provider=SearchProviderName.TAVILY, **kwargs)


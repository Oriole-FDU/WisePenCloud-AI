from chat.application.tools.web.services.web_search.cache import SearchCache
from chat.application.tools.web.services.web_search.enums import ProviderMode, SearchPurpose
from chat.application.tools.web.services.web_search.runtime.runner.base import BaseSearchRunner
from chat.application.tools.web.services.web_search.searcher.fourget import FourGetSearcher


class FourGetSearchRunner(BaseSearchRunner):

    def __init__(
        self,
        *,
        searcher: FourGetSearcher,
        cache: SearchCache,
        provider_mode: ProviderMode = ProviderMode.DEFAULT,
    ) -> None:
        super().__init__(
            searcher=searcher,
            cache=cache,
            purpose=SearchPurpose.RECALL,
            provider_mode=provider_mode,
        )
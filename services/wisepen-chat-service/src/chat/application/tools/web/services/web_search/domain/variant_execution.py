from dataclasses import dataclass

from chat.application.tools.web.services.web_search.domain.query_planning import (
    QueryVariant,
)
from chat.application.tools.web.services.web_search.models import SearchResponse


@dataclass(frozen=True, slots=True)
class VariantSearchResponse:
    """搜索变体结果返回包。"""

    variant: QueryVariant
    response: SearchResponse
    cache_hit: bool = False

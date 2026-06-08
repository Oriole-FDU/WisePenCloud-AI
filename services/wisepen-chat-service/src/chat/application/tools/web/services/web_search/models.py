from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from chat.application.tools.web.services.web_search.enums import (
    ProviderMode,
    SearcherName,
    SearchMode,
)
from chat.application.tools.web.services.web_search.provider_policy.models import (
    CustomProviderCredential,
)


@dataclass(frozen=True, slots=True)
class SearchResult:
    """通用网页搜索结果"""

    title: str
    url: str
    snippet: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """通用搜索响应"""

    query: str
    source: SearcherName | str
    results: List[SearchResult] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchManyRequest:
    """全网搜索多路请求标准强类型 DTO 实体。"""

    queries: List[str]
    mode: SearchMode = SearchMode.NORMAL
    provider_mode: ProviderMode = ProviderMode.DEFAULT
    user_id: Optional[str] = None
    custom_provider_credential: Optional[CustomProviderCredential] = None
    wikipedia_keywords: List[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class WikipediaGroundingResult:
    """Wikipedia 知识图谱结果"""

    keyword: str
    title: str
    extract: str
    url: str
    cache_hit: bool = False



@dataclass(frozen=True, slots=True)
class SearchManyResult:
    """多路搜索结果。"""

    response: SearchResponse
    groundings: List["WikipediaGroundingResult"] = field(default_factory=list)



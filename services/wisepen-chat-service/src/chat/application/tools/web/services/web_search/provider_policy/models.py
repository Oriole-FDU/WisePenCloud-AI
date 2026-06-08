from dataclasses import dataclass
from typing import Optional

from chat.application.tools.web.services.web_search.enums import (
    ProviderMode,
    SearcherName,
)

CUSTOM_PROVIDER_NAMES = frozenset({
    SearcherName.CUSTOM_SERPER,
    SearcherName.TAVILY,
    SearcherName.BRAVE,
    SearcherName.SERPAPI,
    SearcherName.EXA,
    SearcherName.PERPLEXITY,
    SearcherName.ANYSEARCH,
})


@dataclass(frozen=True, slots=True)
class CustomProviderCredential:
    """Custom search provider API credential."""

    provider: SearcherName
    api_key: Optional[str]


@dataclass(frozen=True, slots=True)
class SearchProviderConfig:
    """Runtime search provider configuration snapshot."""

    provider_mode: ProviderMode
    active_provider: Optional[SearcherName] = None
    api_key: Optional[str] = None
    is_valid: bool = True
    error_message: Optional[str] = None

from __future__ import annotations

from pydantic import BaseModel

from chat.application.tools.web_tools.web_search.providers.models import SearchProviderName
from chat.domain.entities.web_search_credential import WebSearchCredentialSource


class CreateWebSearchCredentialRequest(BaseModel):
    provider: SearchProviderName
    source: WebSearchCredentialSource = WebSearchCredentialSource.CUSTOM
    api_key: str


class WebSearchCredentialResponse(BaseModel):
    user_id: str
    provider: SearchProviderName
    source: WebSearchCredentialSource
    is_member: bool
    api_key_masked: str
    api_key_fingerprint: str
    is_active: bool
    created_at: str
    updated_at: str

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from chat.application.tools.web_tools.web_search.providers.models import SearchProviderName


class WebSearchCredentialSource(StrEnum):
    """Web search 凭证来源。"""

    PLATFORM = "platform"
    CUSTOM = "custom"


class WebSearchCredential(Document):
    """用户 Web Search 凭证。

    默认凭证表示用户走平台源，api_key 固定为空字符串；custom 凭证表示用户
    使用自己的搜索源 key。custom key 落库时保存密文，脱敏值只用于 UI 展示。
    """

    user_id: str = Field(..., description="归属用户 ID")
    provider: SearchProviderName = Field(..., description="搜索源类型")
    source: WebSearchCredentialSource = Field(default=WebSearchCredentialSource.PLATFORM, description="凭证来源")
    is_member: bool = Field(default=False, description="是否为可使用平台付费搜索源的会员")
    api_key_ciphertext: str = Field(default="", description="加密后的 API key；平台默认凭证为空字符串")
    api_key_masked: str = Field(default="", description="脱敏后的 API key；只用于 UI 展示")
    api_key_fingerprint: str = Field(default="", description="API key 指纹，平台默认凭证为空字符串")
    is_active: bool = Field(default=True, description="是否启用")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "wisepen_web_search_credentials"
        indexes = [
            IndexModel(
                [("user_id", ASCENDING), ("source", ASCENDING), ("provider", ASCENDING)],
                unique=True,
                name="uniq_web_search_credential_user_source_provider",
            ),
            IndexModel(
                [("user_id", ASCENDING), ("is_active", ASCENDING), ("updated_at", DESCENDING)],
                name="idx_web_search_credential_user_active_updated",
            ),
        ]

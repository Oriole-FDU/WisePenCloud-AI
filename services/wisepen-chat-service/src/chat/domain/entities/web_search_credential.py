from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from chat.application.tools.search_tools.web_search.providers.models import SearchProviderName


class WebSearchCredentialSource(StrEnum):
    """Web search 凭证来源。"""

    PLATFORM_DEFAULT = "platform_default"
    PLATFORM_MEMBER = "platform_member"
    CUSTOM = "custom"


class WebSearchCredential(Document):
    """用户 Web Search 凭证。

    platform_default/platform_member 只表示平台路由类型；custom 凭证表示用户
    使用自己的 provider key。custom key 落库时只保存密文和指纹。
    """

    user_id: str = Field(..., description="归属用户 ID")
    provider: SearchProviderName | None = Field(default=None, description="custom 搜索 provider；平台源为空")
    source: WebSearchCredentialSource = Field(
        default=WebSearchCredentialSource.PLATFORM_DEFAULT,
        description="搜索源类型",
    )
    api_key_ciphertext: str = Field(default="", description="加密后的 API key；平台默认凭证为空字符串")
    api_key_fingerprint: str = Field(default="", description="API key 指纹，平台默认凭证为空字符串")
    is_active: bool = Field(default=True, description="是否启用")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "wisepen_web_search_credentials"
        locators = [
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

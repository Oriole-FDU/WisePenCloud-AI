from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

class ProviderScope(str, Enum):
    SYSTEM = "SYSTEM"  # 平台内置供应商
    USER = "USER"      # 用户自定义供应商

class ProviderType(str, Enum):
    # LLM
    OPENAI_COMPATIBLE_LLM = "OPENAI_COMPATIBLE_LLM"

    # SEARCH_ENGINE
    BRAVE_SEARCH_ENGINE = "BRAVE_SEARCH_ENGINE"
    TAVILY_SEARCH_ENGINE = "TAVILY_SEARCH_ENGINE"


class Provider(Document):
    """
    供应商配置
    """
    name: str = Field(..., description="供应商显示名称")

    api_base_url: str = Field(..., description="API 网关地址")
    api_key: str = Field(..., description="鉴权密钥")
    api_key_fingerprint: Optional[str] = Field(default=None, description="鉴权密钥指纹")

    scope: ProviderScope = Field(default=ProviderScope.SYSTEM, description="供应商作用域")
    owner_user_id: Optional[str] = Field(default=None, description="USER 作用域下的归属用户 ID")

    type: ProviderType = Field(default=ProviderType.OPENAI_COMPATIBLE_LLM, description="供应商能力类型")

    is_active: bool = Field(default=True, description="是否启用")
    usage_tokens: int = Field(default=0, description="累计原始 token 用量")
    billable_usage_tokens: int = Field(default=0, description="累计可计费 token 用量")

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "wisepen_providers"
        indexes = [
            IndexModel(
                [("scope", ASCENDING), ("owner_user_id", ASCENDING), ("is_active", ASCENDING),
                 ("updated_at", DESCENDING)],
                name="idx_provider_scope_owner_active_updated",
            ),
            IndexModel(
                [("scope", ASCENDING), ("type", ASCENDING), ("is_active", ASCENDING)],
                name="idx_provider_scope_type_active",
            ),
            IndexModel(
                [("scope", ASCENDING), ("owner_user_id", ASCENDING), ("name", ASCENDING)],
                unique=True,
                name="uniq_provider_scope_owner_name",
            ),
        ]

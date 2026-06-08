from datetime import datetime, timezone
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from chat.application.tools.web.services.web_search.enums import ProviderMode, SearcherName


class UserSearchProviderConfig(Document):
    """
    用户联网搜索通道配置
    """

    user_id: str = Field(..., description="全局唯一用户 ID")

    provider_mode: ProviderMode = Field(
        default=ProviderMode.DEFAULT,
        description="通道模式"
    )

    provider: Optional[SearcherName] = Field(
        default=None,
        description="自定义模式下选择的搜索服务商厂牌",
    )

    encrypted_api_key: Optional[str] = Field(
        default=None,
        description="AES 加密后的私有密钥密文"
    )

    masked_key: Optional[str] = Field(
        default=None,
        description="用于前端无感回显的脱敏混淆文本"
    )

    is_valid: bool = Field(
        default=False,
        description="当前渠道密钥是否通过过热验证可用"
    )

    last_verified_at: Optional[datetime] = Field(
        default=None,
        description="最近一次通道活性热验证时间"
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="创建时间 (Timezone-aware UTC)"
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="更新时间 (Timezone-aware UTC)"
    )

    class Settings:
        name = "user_search_provider_configs"
        indexes = [
            IndexModel(
                [("user_id", ASCENDING)],
                unique=True,
                name="uniq_user_search_provider_configs_user_id",
            )
        ]
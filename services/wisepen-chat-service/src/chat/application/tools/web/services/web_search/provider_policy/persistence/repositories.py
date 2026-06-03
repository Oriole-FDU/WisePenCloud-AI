from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from chat.application.tools.web.services.web_search.domain.ports import BaseSearchProviderConfigRepository
from chat.application.tools.web.services.web_search.dtos import UserSearchProviderConfigUpsertDTO
from chat.application.tools.web.services.web_search.provider_policy.persistence.entities import UserSearchProviderConfig


class SearchProviderConfigRepository(BaseSearchProviderConfigRepository):
    """用户联网搜索通道配置的 MongoDB 仓储。"""

    async def get_by_user_id(self, user_id: str) -> Optional[UserSearchProviderConfig]:
        """根据用户 ID 查询搜索配置。

        Args:
            user_id: 用户 ID。

        Returns:
            用户搜索配置，不存在时返回 None。
        """
        return await UserSearchProviderConfig.find_one(
            UserSearchProviderConfig.user_id == user_id
        )

    async def upsert(
            self,
            *,
            user_id: str,
            dto: UserSearchProviderConfigUpsertDTO,
    ) -> UserSearchProviderConfig:
        """写入或更新用户的搜索配置。

        若配置不存在则新建，存在则覆盖更新 provider_mode / provider / 加密 Key 等字段。

        Args:
            user_id: 用户 ID。
            dto: 配置更新 DTO。

        Returns:
            更新后的用户搜索配置。
        """
        now = datetime.now(timezone.utc)
        config = await self.get_by_user_id(user_id)

        if config is None:
            # 新建记录分支：将 DTO 的强类型字段清晰、显式地映射组装入 Model，流转清晰可见
            config = UserSearchProviderConfig(
                user_id=user_id,
                provider_mode=dto.provider_mode,
                provider=dto.provider,
                encrypted_api_key=dto.encrypted_api_key,
                masked_key=dto.masked_key,
                is_valid=dto.is_valid,
                created_at=now,
                updated_at=now,
            )
            # Beanie 实体插入
            await config.insert()
            return config

        # 覆盖更新分支
        config.provider_mode = dto.provider_mode
        config.provider = dto.provider
        config.encrypted_api_key = dto.encrypted_api_key
        config.masked_key = dto.masked_key
        config.is_valid = dto.is_valid

        config.updated_at = now

        await config.save()
        return config
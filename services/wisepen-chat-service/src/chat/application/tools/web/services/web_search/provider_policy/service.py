from __future__ import annotations

from typing import Optional

from chat.application.tools.web.services.web_search.enums import ProviderMode, SearcherName
from chat.application.tools.web.services.web_search.provider_policy.encryption import (
    SearchProviderCredentialCipher,
    CredentialDecryptionError,
    CredentialEncryptionError,
)
from chat.application.tools.web.services.web_search.provider_policy.models import (
    SearchProviderConfig,
)
from chat.application.tools.web.services.web_search.provider_policy.persistence import (
    BaseSearchProviderConfigRepository,
    UserSearchProviderConfig,
    UserSearchProviderConfigUpsertDTO,
)
from chat.application.tools.web.services.web_search.provider_policy.validator import (
    SearchProviderConfigValidator,
)
from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException

_ANYSEARCH_ANONYMOUS_ENCRYPTED_KEY = "__anysearch_anonymous__"


class SearchProviderConfigService:
    """用户搜索提供者配置的读写与验证服务。

    协调 Repository（持久化）、Cipher（加解密）、Validator（活性验证）三个组件。
    """
    def __init__(
        self,
        *,
        repository: BaseSearchProviderConfigRepository,
        cipher: SearchProviderCredentialCipher,
        validator: SearchProviderConfigValidator,
    ) -> None:
        """初始化 SearchProviderConfigService。

        Args:
            repository: 配置仓储。
            cipher: 凭证加解密组件。
            validator: 凭证活性校验器。
        """
        self._repository = repository
        self._cipher = cipher
        self._validator = validator

    async def get_config(self, *, user_id: str) -> Optional[UserSearchProviderConfig]:
        """查询指定用户的搜索提供者配置。

        Args:
            user_id: 用户 ID。

        Returns:
            用户搜索配置，未配置时返回 None。
        """
        return await self._repository.get_by_user_id(user_id)

    async def set_mode(self, *, user_id: str, mode: ProviderMode) -> UserSearchProviderConfig:
        """设置用户的搜索提供者模式（DEFAULT / CUSTOM）。

        切换到 CUSTOM 模式时必须已有已配置的自定义提供者。

        Args:
            user_id: 用户 ID。
            mode: 目标模式。

        Returns:
            更新后的用户搜索配置。

        Raises:
            ServiceException: CUSTOM 模式但未配置自定义提供者。
        """
        config = await self._repository.get_by_user_id(user_id)

        if mode == ProviderMode.CUSTOM and (not config or not config.provider):
            raise ServiceException(ChatErrorCode.CUSTOM_PROVIDER_NOT_CONFIGURED)

        return await self._repository.upsert(
            user_id=user_id,
            dto=UserSearchProviderConfigUpsertDTO(
                provider_mode=mode,
                provider=config.provider if config else None,
                encrypted_api_key=config.encrypted_api_key if config else None,
                masked_key=config.masked_key if config else None,
                is_valid=config.is_valid if config else False,
            )
        )

    async def set_custom_provider(
        self,
        *,
        user_id: str,
        provider: SearcherName,
        api_key: str,
    ) -> UserSearchProviderConfig:
        """为用户配置自定义搜索引擎的 API Key。

        对 API Key 加密后存储，ANYSEARCH 匿名模式允许空 Key。

        Args:
            user_id: 用户 ID。
            provider: 搜索引擎名称。
            api_key: API Key 明文。

        Returns:
            更新后的用户搜索配置。

        Raises:
            ServiceException: 加密失败或 Key 为空（非 ANYSEARCH 时）。
        """
        clean_key = api_key.strip()

        if not clean_key and provider != SearcherName.ANYSEARCH:
            raise ServiceException(
                ChatErrorCode.CUSTOM_PROVIDER_NOT_CONFIGURED, custom_msg="自定义搜索源 API Key 不能为空"
            )

        if provider == SearcherName.ANYSEARCH and not clean_key:
            encrypted_key, masked_key = _ANYSEARCH_ANONYMOUS_ENCRYPTED_KEY, "anonymous"
        else:
            try:
                cipher_res = self._cipher.encrypt(user_id=user_id, provider=provider, api_key=clean_key)
                encrypted_key, masked_key = cipher_res.encrypted_key, cipher_res.masked_key
            except CredentialEncryptionError as e:
                raise ServiceException(
                    ChatErrorCode.CUSTOM_PROVIDER_ERROR,
                    custom_msg=str(e),
                ) from None

        return await self._repository.upsert(
            user_id=user_id,
            dto=UserSearchProviderConfigUpsertDTO(
                provider_mode=ProviderMode.CUSTOM,
                provider=provider,
                encrypted_api_key=encrypted_key,
                masked_key=masked_key,
                is_valid=False,
            )
        )

    async def clear_custom_provider(self, *, user_id: str) -> UserSearchProviderConfig:
        """清除用户的自定义搜索引擎配置，重置为 DEFAULT 模式。

        Args:
            user_id: 用户 ID。

        Returns:
            更新后的用户搜索配置。
        """
        return await self._repository.upsert(
            user_id=user_id,
            dto=UserSearchProviderConfigUpsertDTO(provider_mode=ProviderMode.DEFAULT)
        )

    async def verify(self, *, user_id: str) -> UserSearchProviderConfig:
        """验证用户配置的自定义搜索引擎是否可用。

        解密 API Key 后调用 validator 做热连通性校验，更新配置的 is_valid 状态。

        Args:
            user_id: 用户 ID。

        Returns:
            更新后的用户搜索配置（is_valid 反映校验结果）。

        Raises:
            ServiceException: 未配置自定义提供者或解密失败。
        """
        config = await self._repository.get_by_user_id(user_id)
        if not config or not config.provider:
            raise ServiceException(ChatErrorCode.CUSTOM_PROVIDER_NOT_CONFIGURED)

        try:
            api_key = self._decrypt_api_key(config)
        except CredentialDecryptionError as e:
            raise ServiceException(
                ChatErrorCode.CUSTOM_PROVIDER_ERROR,
                custom_msg=str(e),
            ) from None

        is_ok = await self._validator.verify(user_id=user_id, provider=config.provider, api_key=api_key)

        return await self._repository.upsert(
            user_id=user_id,
            dto=UserSearchProviderConfigUpsertDTO(
                provider_mode=config.provider_mode,
                provider=config.provider,
                encrypted_api_key=config.encrypted_api_key,
                masked_key=config.masked_key,
                is_valid=is_ok,
            )
        )

    async def runtime_context(
        self,
        *,
        user_id: str,
    ) -> SearchProviderConfig:
        """构建搜索运行时的提供者配置上下文。

        查询用户配置，解密凭据，返回结构化的 SearchProviderConfig 快照供下游使用。

        Args:
            user_id: 用户 ID。

        Returns:
            运行时配置快照，包含模式、provider、API Key 及有效性状态。
        """
        config = await self._repository.get_by_user_id(user_id)

        if not config or config.provider_mode == ProviderMode.DEFAULT:
            return SearchProviderConfig(provider_mode=ProviderMode.DEFAULT)

        if not config.provider or not config.encrypted_api_key:
            return SearchProviderConfig(
                provider_mode=ProviderMode.CUSTOM,
                is_valid=False,
                error_message="Missing or corrupted custom search provider credential.",
            )

        try:
            api_key = self._decrypt_api_key(config)
        except CredentialDecryptionError as e:
            await self._repository.upsert(
                user_id=user_id,
                dto=UserSearchProviderConfigUpsertDTO(
                    provider_mode=config.provider_mode,
                    provider=config.provider,
                    encrypted_api_key=config.encrypted_api_key,
                    masked_key=config.masked_key,
                    is_valid=False,
                )
            )
            return SearchProviderConfig(
                provider_mode=ProviderMode.CUSTOM,
                is_valid=False,
                error_message=f"Credential decryption exploded: {e}",
            )

        return SearchProviderConfig(
            provider_mode=ProviderMode.CUSTOM,
            active_provider=config.provider,
            api_key=api_key,
            is_valid=config.is_valid,
        )

    def _decrypt_api_key(self, config: UserSearchProviderConfig) -> str:
        """解密配置中存储的 API Key。

        ANYSEARCH 匿名模式直接返回空字符串，其他 provider 调用 cipher 解密。

        Args:
            config: 用户搜索配置。

        Returns:
            API Key 明文。
        """
        if config.provider == SearcherName.ANYSEARCH and config.encrypted_api_key == _ANYSEARCH_ANONYMOUS_ENCRYPTED_KEY:
            return ""

        assert config.provider is not None
        assert config.encrypted_api_key is not None
        return self._cipher.decrypt(
            user_id=config.user_id,
            provider=config.provider,
            encrypted_api_key=config.encrypted_api_key,
        )

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

from pymongo.errors import DuplicateKeyError

from chat.application.tools.web_tools.web_search.providers.models import SearchProviderName
from chat.core.security import SecretCipher, SecretCipherError
from chat.domain.entities.web_search_credential import (
    WebSearchCredential,
    WebSearchCredentialSource,
)
from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException


class MongoWebSearchCredentialRepository:
    """Web search 用户凭证 MongoDB 仓储。"""

    def __init__(self, *, secret_cipher: SecretCipher) -> None:
        self._secret_cipher = secret_cipher

    async def init_platform_credential(
            self,
            *,
            user_id: str,
    ) -> WebSearchCredential:
        # 1. 优先检索是否已存在平台默认凭证
        credential = await WebSearchCredential.find_one(
            WebSearchCredential.user_id == user_id,
            WebSearchCredential.source == WebSearchCredentialSource.PLATFORM,
            WebSearchCredential.provider == SearchProviderName.FOUGET_DDG,
        )
        if credential is not None:
            return credential

        # 2. 构造初始无感平台搜索凭证 (默认使用 FOUGET_DDG)
        now = datetime.now(timezone.utc)
        credential = WebSearchCredential(
            user_id=user_id,
            provider=SearchProviderName.FOUGET_DDG,
            source=WebSearchCredentialSource.PLATFORM,
            is_member=False,
            api_key_ciphertext="",
            api_key_masked="",
            api_key_fingerprint="",
            created_at=now,
            updated_at=now,
        )

        try:
            await credential.insert()
        except DuplicateKeyError:
            # 高并发场景下，防止其他请求先一步插入导致冲突，此处进行降级兜底查询
            credential = await WebSearchCredential.find_one(
                WebSearchCredential.user_id == user_id,
                WebSearchCredential.source == WebSearchCredentialSource.PLATFORM,
                WebSearchCredential.provider == SearchProviderName.FOUGET_DDG,
            )
            if credential is None:
                raise

        return credential

    async def upsert_custom_credential(
            self,
            *,
            user_id: str,
            provider: SearchProviderName,
            api_key: str,
    ) -> WebSearchCredential:
        # 1. 前置业务策略校验
        if provider == SearchProviderName.FOUGET_DDG:
            raise ServiceException(
                ChatErrorCode.WEB_SEARCH_CREDENTIAL_INVALID,
                custom_msg="4get+ddg 是平台默认搜索源，不接受用户自定义 api_key",
            )

        api_key = api_key.strip()
        if not api_key:
            raise ServiceException(
                ChatErrorCode.WEB_SEARCH_CREDENTIAL_INVALID,
                custom_msg="custom 搜索凭证 api_key 必填",
            )

        # 2. 敏感资产加密处理
        try:
            api_key_ciphertext = self._secret_cipher.encrypt(api_key)
        except SecretCipherError as exc:
            raise ServiceException(
                ChatErrorCode.WEB_SEARCH_CREDENTIAL_INVALID,
                custom_msg=str(exc),
            ) from exc

        # 3. 检索旧的自定义凭证并进行 upsert 路由
        now = datetime.now(timezone.utc)
        credential = await WebSearchCredential.find_one(
            WebSearchCredential.user_id == user_id,
            WebSearchCredential.source == WebSearchCredentialSource.CUSTOM,
            WebSearchCredential.provider == provider,
        )

        # 分支 A: 创建全新的自定义凭证
        if credential is None:
            credential = WebSearchCredential(
                user_id=user_id,
                provider=provider,
                source=WebSearchCredentialSource.CUSTOM,
                is_member=False,
                api_key_ciphertext=api_key_ciphertext,
                api_key_masked=self._mask_api_key(api_key),
                api_key_fingerprint=self._fingerprint_api_key(api_key),
                created_at=now,
                updated_at=now,
            )
            await credential.insert()
            return credential

        # 分支 B: 覆盖并激活旧凭证
        credential.api_key_ciphertext = api_key_ciphertext
        credential.api_key_masked = self._mask_api_key(api_key)
        credential.api_key_fingerprint = self._fingerprint_api_key(api_key)
        credential.is_active = True
        credential.updated_at = now

        await credential.save()
        return credential

    async def get_custom_api_key(
            self,
            *,
            user_id: str,
            provider: SearchProviderName,
    ) -> str:
        # 1. 只读取目前处于激活状态的自定义凭证
        credential = await WebSearchCredential.find_one(
            WebSearchCredential.user_id == user_id,
            WebSearchCredential.source == WebSearchCredentialSource.CUSTOM,
            WebSearchCredential.provider == provider,
            WebSearchCredential.is_active == True,  # noqa: E712
        )
        if credential is None or not credential.api_key_ciphertext:
            raise ServiceException(
                ChatErrorCode.WEB_SEARCH_CREDENTIAL_INVALID,
                custom_msg="custom 搜索凭证不存在",
            )

        # 2. 解密返回明文
        try:
            return self._secret_cipher.decrypt(credential.api_key_ciphertext)
        except SecretCipherError as exc:
            raise ServiceException(
                ChatErrorCode.WEB_SEARCH_CREDENTIAL_INVALID,
                custom_msg=str(exc),
            ) from exc

    async def get_platform_credential(
            self,
            *,
            user_id: str,
    ) -> WebSearchCredential | None:
        return await WebSearchCredential.find_one(
            WebSearchCredential.user_id == user_id,
            WebSearchCredential.source == WebSearchCredentialSource.PLATFORM,
        )

    async def set_platform_membership(
            self,
            *,
            user_id: str,
            is_member: bool,
    ) -> WebSearchCredential:
        """设置平台搜索会员态。

        会员态属于平台凭证，不应由 custom credential 的增改过程隐式覆盖。
        这里仅服务本地/内测 UI 的搜索源联调；真实订阅服务上线后，应由统一订阅域管理会员状态。
        """
        credential = await self.init_platform_credential(user_id=user_id)

        # 状态变更分流：会员使用高级 Exa 搜索，非会员回退到默认 4get+ddg
        credential.is_member = is_member
        credential.provider = (
            SearchProviderName.EXA
            if is_member
            else SearchProviderName.FOUGET_DDG
        )
        credential.updated_at = datetime.now(timezone.utc)

        await credential.save()
        return credential

    async def cancel_platform_membership(
            self,
            *,
            user_id: str,
    ) -> WebSearchCredential:
        return await self.set_platform_membership(
            user_id=user_id,
            is_member=False,
        )

    async def set_active_credential(
            self,
            *,
            user_id: str,
            source: WebSearchCredentialSource,
            provider: SearchProviderName,
    ) -> WebSearchCredential:
        now = datetime.now(timezone.utc)

        if source == WebSearchCredentialSource.PLATFORM:
            credential = await self.init_platform_credential(user_id=user_id)
            customs = await WebSearchCredential.find(
                WebSearchCredential.user_id == user_id,
                WebSearchCredential.source == WebSearchCredentialSource.CUSTOM,
                WebSearchCredential.is_active == True,  # noqa: E712
            ).to_list()
            for custom in customs:
                custom.is_active = False
                custom.updated_at = now
                await custom.save()
            return credential

        credential = await WebSearchCredential.find_one(
            WebSearchCredential.user_id == user_id,
            WebSearchCredential.source == WebSearchCredentialSource.CUSTOM,
            WebSearchCredential.provider == provider,
        )
        if credential is None:
            raise ServiceException(
                ChatErrorCode.WEB_SEARCH_CREDENTIAL_INVALID,
                custom_msg="custom 搜索凭证不存在",
            )

        customs = await WebSearchCredential.find(
            WebSearchCredential.user_id == user_id,
            WebSearchCredential.source == WebSearchCredentialSource.CUSTOM,
        ).to_list()
        for custom in customs:
            custom.is_active = custom.provider == provider
            custom.updated_at = now
            await custom.save()

        return credential

    async def get_active_custom_credential(
            self,
            *,
            user_id: str,
    ) -> WebSearchCredential | None:
        """读取当前运行期应使用的 active custom 凭证，不在仓储层解密。"""
        return await WebSearchCredential.find(
            WebSearchCredential.user_id == user_id,
            WebSearchCredential.source == WebSearchCredentialSource.CUSTOM,
            WebSearchCredential.is_active == True,  # noqa: E712
        ).sort("-updated_at").first_or_none()

    async def list_user_credentials(
            self,
            *,
            user_id: str,
    ) -> list[WebSearchCredential]:
        # 优先展示激活的、以及最新修改的凭证列表
        return await WebSearchCredential.find(
            WebSearchCredential.user_id == user_id,
        ).sort("-is_active", "-updated_at").to_list()

    @staticmethod
    def _mask_api_key(api_key: str) -> str:
        """对秘钥进行脱敏展示，保留前后各 4 位。"""
        if len(api_key) <= 8:
            return "*" * len(api_key)
        return f"{api_key[:4]}***{api_key[-4:]}"

    @staticmethod
    def _fingerprint_api_key(api_key: str) -> str:
        """计算秘钥摘要，以便在不解密明文的情况下比对凭证是否发生变更。"""
        return sha256(api_key.encode("utf-8")).hexdigest()

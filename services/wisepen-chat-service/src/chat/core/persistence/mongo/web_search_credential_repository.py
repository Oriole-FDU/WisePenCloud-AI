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
    """Web search 用户凭证 MongoDB 仓储。

    数据模型：每个 (user_id, source, provider) 是独立、稳定的文档。
    - PLATFORM 下 FOUGET_DDG 和 EXA 各自一条文档，会员切换通过 is_active 切换文档。
    - CUSTOM 下每个 provider 各自一条文档。
    运行期通过 is_active 字段保证同一 user 仅一条凭证为激活态。
    """

    def __init__(self, *, secret_cipher: SecretCipher) -> None:
        self._secret_cipher = secret_cipher

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def init_platform_credential(
            self,
            *,
            user_id: str,
    ) -> WebSearchCredential:
        """确保默认 FOUGET_DDG 平台凭证存在，用于设置面板首次展示兜底。"""
        has_active_custom = await WebSearchCredential.find_one(
            WebSearchCredential.user_id == user_id,
            WebSearchCredential.source == WebSearchCredentialSource.CUSTOM,
            WebSearchCredential.is_active == True,  # noqa: E712
        ) is not None

        return await self._get_or_create_platform_credential(
            user_id=user_id,
            provider=SearchProviderName.FOUGET_DDG,
            is_active_default=not has_active_custom,
        )

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
            return await self._ensure_single_active(user_id=user_id, target=credential)

        # 分支 B: 覆盖旧凭证
        credential.api_key_ciphertext = api_key_ciphertext
        credential.api_key_masked = self._mask_api_key(api_key)
        credential.api_key_fingerprint = self._fingerprint_api_key(api_key)
        credential.updated_at = now

        return await self._ensure_single_active(user_id=user_id, target=credential)

    async def get_custom_api_key(
            self,
            *,
            user_id: str,
            provider: SearchProviderName,
    ) -> str:
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
        """返回当前生效的 PLATFORM 凭证（可能是 FOUGET_DDG 或 EXA，取决于会员态）。"""
        return await WebSearchCredential.find_one(
            WebSearchCredential.user_id == user_id,
            WebSearchCredential.source == WebSearchCredentialSource.PLATFORM,
            WebSearchCredential.is_active == True,  # noqa: E712
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
        provider = SearchProviderName.EXA if is_member else SearchProviderName.FOUGET_DDG
        credential = await self._get_or_create_platform_credential(
            user_id=user_id,
            provider=provider,
            is_active_default=False,
        )
        credential.is_member = is_member
        credential.updated_at = datetime.now(timezone.utc)

        # 订阅/取消订阅都会切换为平台源，因此强制走单激活不变量。
        return await self._ensure_single_active(user_id=user_id, target=credential)

    async def set_active_credential(
            self,
            *,
            user_id: str,
            source: WebSearchCredentialSource,
            provider: SearchProviderName,
    ) -> WebSearchCredential:
        if source == WebSearchCredentialSource.PLATFORM:
            credential = await self._get_or_create_platform_credential(
                user_id=user_id,
                provider=provider,
                is_active_default=False,
            )
        else:
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

        credential.updated_at = datetime.now(timezone.utc)
        return await self._ensure_single_active(user_id=user_id, target=credential)

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
        """优先展示激活的、以及最新修改的凭证列表。"""
        return await WebSearchCredential.find(
            WebSearchCredential.user_id == user_id,
        ).sort("-is_active", "-updated_at").to_list()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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

    async def _get_or_create_platform_credential(
            self,
            *,
            user_id: str,
            provider: SearchProviderName,
            is_active_default: bool,
    ) -> WebSearchCredential:
        """按 provider 获取或创建 PLATFORM 凭证（不修改 active 态）。

        每个 (user, PLATFORM, provider) 是独立、稳定的文档，
        会员态切换通过切换 active 的文档实现，而不是原地改写 provider 字段。
        """
        credential = await WebSearchCredential.find_one(
            WebSearchCredential.user_id == user_id,
            WebSearchCredential.source == WebSearchCredentialSource.PLATFORM,
            WebSearchCredential.provider == provider,
        )
        if credential is not None:
            return credential

        now = datetime.now(timezone.utc)
        credential = WebSearchCredential(
            user_id=user_id,
            provider=provider,
            source=WebSearchCredentialSource.PLATFORM,
            is_member=provider == SearchProviderName.EXA,
            is_active=is_active_default,
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
                WebSearchCredential.provider == provider,
            )
            if credential is None:
                raise
        return credential

    async def _ensure_single_active(
            self,
            *,
            user_id: str,
            target: WebSearchCredential,
    ) -> WebSearchCredential:
        """保证同一 user 仅存在一个 is_active=True 凭证。

        将 target 强制置为激活态，并把同 user 的其他激活态凭证（跨 PLATFORM/CUSTOM）全部置为 False。
        """
        now = datetime.now(timezone.utc)
        target.is_active = True
        target.updated_at = now
        await target.save()

        others = await WebSearchCredential.find(
            WebSearchCredential.user_id == user_id,
            WebSearchCredential.id != target.id,
            WebSearchCredential.is_active == True,  # noqa: E712
        ).to_list()
        for other in others:
            other.is_active = False
            other.updated_at = now
            await other.save()

        return target

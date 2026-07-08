from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256

from beanie.operators import In
from pymongo.errors import DuplicateKeyError

from chat.application.tools.search_tools.web_search.providers.models import SearchProviderName
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
    - PLATFORM_DEFAULT 和 PLATFORM_MEMBER 是平台路由类型，不绑定具体 provider。
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
            source: WebSearchCredentialSource = WebSearchCredentialSource.PLATFORM_DEFAULT,
    ) -> WebSearchCredential:
        """确保平台路由凭证存在，用于设置面板首次展示兜底。"""
        has_active_credential = await WebSearchCredential.find_one(
            WebSearchCredential.user_id == user_id,
            WebSearchCredential.is_active == True,  # noqa: E712
        ) is not None

        return await self._get_or_create_platform_credential(
            user_id=user_id,
            source=source,
            is_active_default=source == WebSearchCredentialSource.PLATFORM_DEFAULT and not has_active_credential,
        )

    async def upsert_custom_credential(
            self,
            *,
            user_id: str,
            provider: SearchProviderName,
            api_key: str,
    ) -> WebSearchCredential:
        # 1. 前置业务策略校验
        if not provider.supports_custom_credential:
            raise ServiceException(
                ChatErrorCode.WEB_SEARCH_CREDENTIAL_INVALID,
                custom_msg="该 provider 不接受用户自定义 api_key",
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
                api_key_ciphertext=api_key_ciphertext,
                api_key_fingerprint=self._fingerprint_api_key(api_key),
                created_at=now,
                updated_at=now,
            )
            await credential.insert()
            return await self._ensure_single_active(user_id=user_id, target=credential)

        # 分支 B: 覆盖旧凭证
        credential.api_key_ciphertext = api_key_ciphertext
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
        """返回当前生效的平台路由凭证。"""
        return await WebSearchCredential.find_one(
            WebSearchCredential.user_id == user_id,
            In(
                WebSearchCredential.source,
                [
                    WebSearchCredentialSource.PLATFORM_DEFAULT,
                    WebSearchCredentialSource.PLATFORM_MEMBER,
                ],
            ),
            WebSearchCredential.is_active == True,  # noqa: E712
        )

    async def set_active_credential(
            self,
            *,
            user_id: str,
            source: WebSearchCredentialSource,
            provider: SearchProviderName | None = None,
    ) -> WebSearchCredential:
        if source in {
            WebSearchCredentialSource.PLATFORM_DEFAULT,
            WebSearchCredentialSource.PLATFORM_MEMBER,
        }:
            credential = await self._get_or_create_platform_credential(
                user_id=user_id,
                source=source,
                is_active_default=False,
            )
        else:
            if provider is None:
                raise ServiceException(
                    ChatErrorCode.WEB_SEARCH_CREDENTIAL_INVALID,
                    custom_msg="custom 搜索凭证 provider 必填",
                )
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
    def _fingerprint_api_key(api_key: str) -> str:
        """计算秘钥摘要，以便在不解密明文的情况下比对凭证是否发生变更。"""
        return sha256(api_key.encode("utf-8")).hexdigest()

    async def _get_or_create_platform_credential(
            self,
            *,
            user_id: str,
            source: WebSearchCredentialSource,
            is_active_default: bool,
    ) -> WebSearchCredential:
        """获取或创建平台路由凭证（不修改 active 态）。"""
        credential = await WebSearchCredential.find_one(
            WebSearchCredential.user_id == user_id,
            WebSearchCredential.source == source,
            WebSearchCredential.provider == None,  # noqa: E711
        )
        if credential is not None:
            return credential

        now = datetime.now(timezone.utc)
        credential = WebSearchCredential(
            user_id=user_id,
            provider=None,
            source=source,
            is_active=is_active_default,
            api_key_ciphertext="",
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
                WebSearchCredential.source == source,
                WebSearchCredential.provider == None,  # noqa: E711
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

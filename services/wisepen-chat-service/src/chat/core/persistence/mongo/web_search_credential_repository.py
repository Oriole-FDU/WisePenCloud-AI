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

    async def get_or_create_default_platform_credential(
        self,
        *,
        user_id: str,
    ) -> WebSearchCredential:
        credential = await WebSearchCredential.find_one(
            WebSearchCredential.user_id == user_id,
            WebSearchCredential.source == WebSearchCredentialSource.PLATFORM,
            WebSearchCredential.provider == SearchProviderName.FOURGET,
        )
        if credential is not None:
            return credential

        now = datetime.now(timezone.utc)
        credential = WebSearchCredential(
            user_id=user_id,
            provider=SearchProviderName.FOURGET,
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
            credential = await WebSearchCredential.find_one(
                WebSearchCredential.user_id == user_id,
                WebSearchCredential.source == WebSearchCredentialSource.PLATFORM,
                WebSearchCredential.provider == SearchProviderName.FOURGET,
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
        if provider == SearchProviderName.FOURGET:
            raise ServiceException(
                ChatErrorCode.WEB_SEARCH_CREDENTIAL_INVALID,
                custom_msg="4get 是平台默认搜索源，不接受用户自定义 api_key",
            )

        api_key = api_key.strip()
        if not api_key:
            raise ServiceException(
                ChatErrorCode.WEB_SEARCH_CREDENTIAL_INVALID,
                custom_msg="custom 搜索凭证 api_key 必填",
            )
        try:
            api_key_ciphertext = self._secret_cipher.encrypt(api_key)
        except SecretCipherError as exc:
            raise ServiceException(
                ChatErrorCode.WEB_SEARCH_CREDENTIAL_INVALID,
                custom_msg=str(exc),
            ) from exc

        now = datetime.now(timezone.utc)
        credential = await WebSearchCredential.find_one(
            WebSearchCredential.user_id == user_id,
            WebSearchCredential.source == WebSearchCredentialSource.CUSTOM,
            WebSearchCredential.provider == provider,
        )
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

        credential.is_member = False
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

    async def list_user_credentials(
        self,
        *,
        user_id: str,
    ) -> list[WebSearchCredential]:
        return await WebSearchCredential.find(
            WebSearchCredential.user_id == user_id,
        ).sort("-is_active", "-updated_at").to_list()

    @staticmethod
    def _mask_api_key(api_key: str) -> str:
        if len(api_key) <= 8:
            return "*" * len(api_key)
        return f"{api_key[:4]}***{api_key[-4:]}"

    @staticmethod
    def _fingerprint_api_key(api_key: str) -> str:
        return sha256(api_key.encode("utf-8")).hexdigest()

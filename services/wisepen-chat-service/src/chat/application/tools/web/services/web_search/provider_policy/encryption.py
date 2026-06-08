from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from chat.application.tools.web.services.web_search.enums import SearcherName

_CIPHERTEXT_VERSION = "v1"
_NONCE_SIZE_BYTES = 12
_AES_256_KEY_SIZE_BYTES = 32

MASTER_KEY_REQUIRED_MESSAGE = (
    "SEARCH_PROVIDER_CREDENTIAL_MASTER_KEY is required when using stored custom "
    "search provider credentials."
)


class CredentialCipherError(Exception):
    """加解密组件通用基础异常。"""
    pass


class CredentialEncryptionError(CredentialCipherError):
    """加密失败。"""
    pass


class CredentialDecryptionError(CredentialCipherError):
    """解密失败。"""
    pass


@dataclass(frozen=True, slots=True)
class CipherResult:
    """加密结果 DTO，包含密文和脱敏 Key。"""
    encrypted_key: str
    masked_key: str


class SearchProviderCredentialCipher:
    """用户私有搜索渠道 API Key 的 AES-256-GCM 加解密组件。

    使用 AAD（附加认证数据）绑定 user_id 和 provider。
    master_key 统一使用无前缀 base64url 编码的 32 字节密钥。
    """

    def __init__(self, *, master_key: Optional[str], key_id: Optional[str]) -> None:
        """初始化 SearchProviderCredentialCipher。

        Args:
            master_key: 主密钥字符串（无前缀 base64url 编码）。
            key_id: 密钥标识符，用于区分不同版本的密钥。
        """
        self._raw_master_key = master_key
        self._key_id = (key_id or "").strip()
        self._master_key: Optional[bytes] = None

    @property
    def key_id(self) -> str:
        """返回当前使用的密钥 ID，不存在时抛出 CredentialEncryptionError。"""
        return self._key_id_str(CredentialEncryptionError)

    def encrypt(self, *, user_id: str, provider: SearcherName, api_key: str) -> CipherResult:
        """加密 API Key，返回包含密文、脱敏 Key 和密钥 ID 的 CipherResult。

        使用 AES-256-GCM 加密，AAD 绑定 user_id 和 provider，
        同时对 API Key 做脱敏处理用于前端展示。

        Args:
            user_id: 用户 ID（作为 AAD 的一部分）。
            provider: 搜索引擎名称（作为 AAD 的一部分）。
            api_key: 待加密的 API Key。

        Returns:
            加密结果 DTO。

        Raises:
            CredentialEncryptionError: 加密失败。
        """
        normalized = api_key.strip()
        if not normalized:
            raise CredentialEncryptionError("api_key is required")

        master_key = self._master_key_bytes(CredentialEncryptionError)
        key_id = self._key_id_str(CredentialEncryptionError)
        nonce = os.urandom(_NONCE_SIZE_BYTES)

        # AAD 严格对齐 user_id 和 provider 字符串
        ciphertext = AESGCM(master_key).encrypt(
            nonce, normalized.encode(), f"{user_id.strip()}:{provider.value}".encode()
        )

        # 优雅的就地混淆脱敏
        suffix = normalized[-4:] if len(normalized) > 8 else ""
        masked = f"{normalized[:4]}...{suffix}" if suffix else f"{normalized[:4]}..."

        return CipherResult(
            encrypted_key=":".join(
                (
                    _CIPHERTEXT_VERSION,
                    base64.urlsafe_b64encode(nonce).decode("ascii").rstrip("="),
                    base64.urlsafe_b64encode(ciphertext).decode("ascii").rstrip("="),
                )
            ),
            masked_key=masked,
        )

    def decrypt(
            self,
            *,
            user_id: str,
            provider: SearcherName,
            encrypted_api_key: str,
    ) -> str:
        """解密 API Key。

        Args:
            user_id: 用户 ID（作为 AAD 验证的一部分）。
            provider: 搜索引擎名称（作为 AAD 验证的一部分）。
            encrypted_api_key: 密文 API Key。

        Returns:
            解密后的 API Key 明文。

        Raises:
            CredentialDecryptionError: 解密失败（格式错误、完整性校验失败）。
        """
        master_key = self._master_key_bytes(CredentialDecryptionError)

        try:
            version, nonce_text, ciphertext_text = encrypted_api_key.split(":", 2)
        except ValueError:
            raise CredentialDecryptionError("invalid encrypted api key format") from None

        if version != _CIPHERTEXT_VERSION:
            raise CredentialDecryptionError("unsupported encrypted api key version")

        try:
            nonce = base64.b64decode(
                nonce_text + "=" * (-len(nonce_text) % 4),
                altchars=b"-_",
                validate=True,
            )
            ciphertext = base64.b64decode(
                ciphertext_text + "=" * (-len(ciphertext_text) % 4),
                altchars=b"-_",
                validate=True,
            )
            plaintext = AESGCM(master_key).decrypt(
                nonce,
                ciphertext,
                f"{user_id.strip()}:{provider.value}".encode(),
            )
        except (binascii.Error, ValueError, InvalidTag):
            raise CredentialDecryptionError("failed to decrypt api key") from None

        return plaintext.decode()

    def _master_key_bytes(self, error) -> bytes:
        """惰性加载并缓存解析后的主密钥字节。

        Args:
            error: 用于包装解析失败的异常类型。

        Returns:
            32 字节的 AES-256 密钥。
        """
        if self._master_key is None:
            try:
                key = _decode_master_key(self._raw_master_key)
                self._master_key = key
            except CredentialEncryptionError as e:
                raise error(str(e)) from None
            return key

        return self._master_key

    def _key_id_str(self, error) -> str:
        """返回密钥 ID，不存在时抛出指定异常。

        Args:
            error: 密钥 ID 缺失时抛出的异常类型。

        Returns:
            密钥 ID 字符串。
        """
        if not self._key_id:
            raise error(
                "SEARCH_PROVIDER_CREDENTIAL_KEY_ID is required when using stored "
                "custom search provider credentials."
            )
        return self._key_id



def _decode_master_key(raw: Optional[str]) -> bytes:
    """解析无前缀 base64url 主密钥字符串。

    Args:
        raw: 主密钥字符串。

    Returns:
        32 字节的 AES-256 密钥。

    Raises:
        CredentialEncryptionError: 格式无效或解码后长度不为 32 字节。
    """
    value = (raw or "").strip()
    if not value:
        raise CredentialEncryptionError(MASTER_KEY_REQUIRED_MESSAGE)

    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError):
        raise CredentialEncryptionError(
            "SEARCH_PROVIDER_CREDENTIAL_MASTER_KEY must be a valid unprefixed "
            "base64url encoded 32-byte key"
        ) from None

    if len(decoded) != _AES_256_KEY_SIZE_BYTES:
        raise CredentialEncryptionError(
            "SEARCH_PROVIDER_CREDENTIAL_MASTER_KEY must decode to 32 bytes"
        )
    return decoded

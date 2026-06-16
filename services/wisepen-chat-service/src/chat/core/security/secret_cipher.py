from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretCipherError(RuntimeError):
    """密钥加解密失败。"""


class SecretCipher:
    """通用对称加解密服务。

    Fernet 使用认证加密，密文自带完整性校验；主密钥必须来自配置或密钥系统，
    不能和密文一起存入数据库。
    """

    def __init__(self, *, encryption_key: str) -> None:
        self._encryption_key = encryption_key.strip()
        self._fernet: Fernet | None = None

    def encrypt(self, plaintext: str) -> str:
        cipher = self._get_cipher()
        return cipher.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        cipher = self._get_cipher()
        try:
            return cipher.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise SecretCipherError("密文无法解密，可能密钥不匹配或数据已损坏") from exc

    def _get_cipher(self) -> Fernet | None:
        if not self._encryption_key:
            raise SecretCipherError("缺少 SECRET_ENCRYPTION_KEY 配置")
        if self._fernet is None:
            try:
                self._fernet = Fernet(self._encryption_key.encode("utf-8"))
            except (ValueError, TypeError) as exc:
                raise SecretCipherError("SECRET_ENCRYPTION_KEY 不是合法 Fernet key") from exc
        return self._fernet

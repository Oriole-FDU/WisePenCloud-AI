from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from chat.application.tools.web_tools.web_search.providers.models import SearchProviderName
from chat.core.security import SecretCipherError
from chat.domain.entities.web_search_credential import WebSearchCredentialSource


class WebSearchMode(StrEnum):
    """联网搜索运行模式：区分平台官方默认源与用户自定义 API 密钥源。"""
    PLATFORM = "platform"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class WebSearchRuntimeConfig:
    """运行期固化的搜索配置上下文快照（ api_key 绝对隔离，不对模型可见）。"""
    user_id: str
    session_id: str
    search_config_id: str
    search_mode: WebSearchMode
    provider: SearchProviderName
    source_id: str
    api_key: str | None = None
    credential_fingerprint: str | None = None
    credential_updated_at: str | None = None
    is_valid: bool = True
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class _CachedCredential:
    api_key: str
    version_key: str


@dataclass(frozen=True, slots=True)
class _PlatformProviderChoice:
    provider: SearchProviderName
    source_id: str
    api_key: str | None


@runtime_checkable
class WebSearchCredentialRecord(Protocol):
    """搜索凭证领域实体协议拓扑定义。"""
    provider: SearchProviderName
    source: WebSearchCredentialSource | str
    api_key_ciphertext: str
    api_key_fingerprint: str
    is_active: bool
    updated_at: datetime | None


class WebSearchCredentialRuntimeRepository(Protocol):
    """运行期配置层所需的凭证仓储最小接口。"""

    async def get_platform_credential(self, *, user_id: str) -> WebSearchCredentialRecord | None: ...

    async def get_active_custom_credential(self, *, user_id: str) -> WebSearchCredentialRecord | None: ...


class WebSearchCredentialCipher(Protocol):
    """运行期密文安全解密内核抽象。"""

    def decrypt(self, ciphertext: str) -> str: ...


class WebSearchRuntimeContextResolver:
    """搜索配置解析器：拦截高频重复解密，统一在内存中维护失效检验缓存。"""

    __slots__ = (
        "_credential_repository",
        "_cipher",
        "_decrypted_credentials",
        "_platform_exa_enabled",
        "_platform_exa_api_key",
    )

    def __init__(
            self,
            *,
            credential_repository: WebSearchCredentialRuntimeRepository,
            cipher: WebSearchCredentialCipher,
            platform_exa_enabled: bool = False,
            platform_exa_api_key: str | None = None,
    ) -> None:
        self._credential_repository = credential_repository
        self._cipher = cipher
        self._platform_exa_enabled = platform_exa_enabled
        self._platform_exa_api_key = (platform_exa_api_key or "").strip() or None
        self._decrypted_credentials: dict[
            tuple[str, str, SearchProviderName],
            _CachedCredential,
        ] = {}

    async def resolve(
            self,
            *,
            user_id: str,
            session_id: str,
    ) -> WebSearchRuntimeConfig:
        """多路分流解析：优先引导至自定义密钥，否则平滑退化至平台全局分流。"""
        credential = await self._credential_repository.get_active_custom_credential(user_id=user_id)

        # 1. 平台默认分流路径：无自定义凭证时直接短路返回
        if credential is None:
            platform_choice = await self._resolve_platform_provider(user_id=user_id)
            return WebSearchRuntimeConfig(
                user_id=user_id,
                session_id=session_id,
                search_config_id=platform_choice.source_id,
                search_mode=WebSearchMode.PLATFORM,
                provider=platform_choice.provider,
                source_id=platform_choice.source_id,
                api_key=platform_choice.api_key,
            )

        # 2. 自定义凭证路径：基于元数据特征生成唯一版本识别键
        search_config_id = f"custom:{credential.provider.value}"
        version_key = _credential_version_key(credential)
        cache_key = (user_id, search_config_id, credential.provider)

        cached = self._decrypted_credentials.get(cache_key)

        # 3. 缓存失效判定机制：无记录或最新哈希版本对齐失败，触发安全解密
        if cached is None or cached.version_key != version_key:
            try:
                api_key = self._cipher.decrypt(credential.api_key_ciphertext)
            except SecretCipherError as exc:
                return WebSearchRuntimeConfig(
                    user_id=user_id,
                    session_id=session_id,
                    search_config_id=search_config_id,
                    search_mode=WebSearchMode.CUSTOM,
                    provider=credential.provider,
                    source_id=_custom_source_id(credential),
                    credential_fingerprint=credential.api_key_fingerprint,
                    credential_updated_at=_datetime_to_version(credential.updated_at),
                    is_valid=False,
                    error_message=str(exc),
                )
            cached = _CachedCredential(api_key=api_key, version_key=version_key)
            self._decrypted_credentials[cache_key] = cached

        return WebSearchRuntimeConfig(
            user_id=user_id,
            session_id=session_id,
            search_config_id=search_config_id,
            search_mode=WebSearchMode.CUSTOM,
            provider=credential.provider,
            source_id=_custom_source_id(credential),
            api_key=cached.api_key,
            credential_fingerprint=credential.api_key_fingerprint,
            credential_updated_at=_datetime_to_version(credential.updated_at),
            is_valid=credential.is_active,
        )

    async def _resolve_platform_provider(
            self,
            *,
            user_id: str,
    ) -> _PlatformProviderChoice:
        """平台级智能降级：评估配置矩阵，在高端引擎（Exa）与通用引擎（4get）间平滑调度。"""
        platform_credential = await self._credential_repository.get_platform_credential(user_id=user_id)
        if platform_credential is None:
            return _PlatformProviderChoice(
                provider=SearchProviderName.FOUGET_DDG,
                source_id="platform:4get_ddg",
                api_key=None,
            )

        # 当并非指定 Exa 引擎、平台开关关闭或本地密钥为空时，强制强制退化至通用引擎
        if (
                platform_credential.provider != SearchProviderName.EXA
                or not self._platform_exa_enabled
                or not self._platform_exa_api_key
        ):
            return _PlatformProviderChoice(
                provider=SearchProviderName.FOUGET_DDG,
                source_id="platform:4get_ddg",
                api_key=None,
            )

        return _PlatformProviderChoice(
            provider=SearchProviderName.EXA,
            source_id="platform:exa",
            api_key=self._platform_exa_api_key,
        )


def _credential_version_key(credential: WebSearchCredentialRecord) -> str:
    """复合多项凭证特征，生成内存明文缓存一致性指纹。"""
    source_val = (
        credential.source.value
        if isinstance(credential.source, WebSearchCredentialSource)
        else str(credential.source)
    )
    return "|".join((
        source_val,
        credential.provider.value,
        _datetime_to_version(credential.updated_at) or "",
        credential.api_key_fingerprint,
    ))


def _custom_source_id(credential: WebSearchCredentialRecord) -> str:
    """安全混淆自定义源唯一标识码。"""
    fingerprint = credential.api_key_fingerprint[:16] if credential.api_key_fingerprint else "unknown"
    return f"custom:{credential.provider.value}:{fingerprint}"


def _datetime_to_version(value: datetime | None) -> str | None:
    """统一转化时间戳为 ISO 标准的版本化标识字串。"""
    return value.isoformat() if value is not None else None
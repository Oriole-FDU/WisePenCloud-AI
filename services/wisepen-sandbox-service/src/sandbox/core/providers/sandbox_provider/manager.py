from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sandbox.domain.interfaces import SandboxProvider, SandboxProviderType


class SandboxProviderManager:
    """
    按 provider_id 注册并路由沙箱 Provider
    具体能力由 SandboxProvider 接口直接暴露
    """

    def __init__(
        self,
        provider_classes: Sequence[type[SandboxProvider]],
        provider_settings: dict[SandboxProviderType, dict[str, Any]],
    ) -> None:
        self._providers: dict[SandboxProviderType, SandboxProvider] = {}
        for provider_class in provider_classes:
            provider_id = provider_class.sandbox_provider_id()
            config = provider_settings.get(provider_id)
            if config is not None:
                self._providers[provider_id] = provider_class.from_config(config)

    def get(self, provider_id: str | SandboxProviderType) -> SandboxProvider:
        provider = self._providers.get(SandboxProviderType(provider_id))
        if provider is None:
            raise ValueError(f"sandbox provider is not configured: {provider_id}")
        return provider

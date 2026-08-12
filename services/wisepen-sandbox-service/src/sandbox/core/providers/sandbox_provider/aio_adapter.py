from __future__ import annotations

import httpx

from sandbox.domain.interfaces import (
    SandboxProvider,
    SandboxProviderInfo,
)


class AIOAdapter(SandboxProvider):
    """All-in-One Sandbox provider adapter."""

    _HEALTH_FIELDS = frozenset(
        {"success", "message", "data", "home_dir", "version", "detail"}
    )

    def __init__(self, sandbox_image: str, health_timeout_seconds: float = 5.0) -> None:
        self._sandbox_image = sandbox_image
        self._health_timeout_seconds = health_timeout_seconds

    def get_sandbox_provider_info(
        self,
        provider_id: str | None = None,
    ) -> SandboxProviderInfo:
        return SandboxProviderInfo(
            image=self._sandbox_image,
        )

    async def check_ready(self, provider_id: str, base_url: str | None) -> bool:
        if base_url is None:
            return False

        try:
            async with httpx.AsyncClient(
                timeout=self._health_timeout_seconds,
                trust_env=False,
            ) as client:
                response = await client.get(f"{base_url.rstrip('/')}/v1/sandbox")
            if response.status_code != 200:
                return False
            payload = response.json()
        except (httpx.HTTPError, httpx.InvalidURL, ValueError):
            return False

        return isinstance(payload, dict) and self._HEALTH_FIELDS.issubset(payload)

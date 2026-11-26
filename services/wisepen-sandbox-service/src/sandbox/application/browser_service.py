from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from common.core.exceptions import ServiceException

from sandbox.application.workspace_manager import WorkspaceManager
from sandbox.core.providers import SandboxProviderManager
from sandbox.domain.entities import SandboxDocument, SandboxState, SessionWorkspaceDocument
from sandbox.domain.error_codes import SandboxErrorCode
from sandbox.domain.interfaces import (
    BrowserActionResult,
    BrowserChannel,
    BrowserContentResult,
    BrowserInfoResult,
    BrowserListResult,
)
from sandbox.domain.repositories import SandboxRepository

_SCREENSHOT_MIME_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}


class SandboxBrowserService:
    """将浏览器 MCP 工具调用映射到沙箱浏览器能力"""

    def __init__(
        self,
        sandbox_repository: SandboxRepository,
        workspace_manager: WorkspaceManager,
        provider_manager: SandboxProviderManager,
    ) -> None:
        self._sandboxes = sandbox_repository
        self._workspace_manager = workspace_manager
        self._providers = provider_manager

    async def get_info(self, *, user_id: str, session_id: str) -> BrowserInfoResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.get_info(
                sandbox.base_url, sandbox.metadata or {},
            )

    @asynccontextmanager
    async def open_cdp_channel(
        self,
        *,
        user_id: str,
        session_id: str,
    ) -> AsyncIterator[BrowserChannel]:
        """为某会话打开到沙箱浏览器 CDP 的双向通道，连接期间持续持有沙箱租约"""
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            async with provider.connect_cdp(
                sandbox.base_url, sandbox.metadata or {},
            ) as channel:
                yield channel

    async def screenshot(
        self,
        *,
        user_id: str,
        session_id: str,
        format: str = "png",
        quality: int | None = None,
    ) -> tuple[str, bytes]:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            with BytesIO() as buffer:
                provider = self._providers.get(sandbox.provider_id)
                await provider.screenshot(
                    sandbox.base_url, buffer,
                    format=format, quality=quality, provider_metadata=sandbox.metadata or {},
                )
                data = buffer.getvalue()
        mime_type = _SCREENSHOT_MIME_TYPES.get(format, "image/png")
        return mime_type, data

    async def execute_action(
        self, *, user_id: str, session_id: str, action: Mapping[str, Any],
    ) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.execute_action(
                sandbox.base_url, action, sandbox.metadata or {},
            )

    async def navigate(
        self,
        *,
        user_id: str,
        session_id: str,
        url: str,
        wait_until: str | None = None,
        timeout: float | None = None,
    ) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.navigate(
                sandbox.base_url, url,
                wait_until=wait_until, timeout=timeout, provider_metadata=sandbox.metadata or {},
            )

    async def back(self, *, user_id: str, session_id: str) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.back(sandbox.base_url, sandbox.metadata or {})

    async def forward(self, *, user_id: str, session_id: str) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.forward(sandbox.base_url, sandbox.metadata or {})

    async def reload(self, *, user_id: str, session_id: str) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.reload(sandbox.base_url, sandbox.metadata or {})

    async def click(
        self,
        *,
        user_id: str,
        session_id: str,
        selector: str | None = None,
        index: int | None = None,
        x: float | None = None,
        y: float | None = None,
        button: str | None = None,
        click_count: int | None = None,
    ) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.click(
                sandbox.base_url,
                selector=selector, index=index, x=x, y=y, button=button, click_count=click_count,
                provider_metadata=sandbox.metadata or {},
            )

    async def fill(
        self,
        *,
        user_id: str,
        session_id: str,
        text: str,
        selector: str | None = None,
        index: int | None = None,
    ) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.fill(
                sandbox.base_url, text,
                selector=selector, index=index, provider_metadata=sandbox.metadata or {},
            )

    async def press_key(self, *, user_id: str, session_id: str, key: str) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.press_key(
                sandbox.base_url, key, sandbox.metadata or {},
            )

    async def hot_key(self, *, user_id: str, session_id: str, keys: list[str]) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.hot_key(
                sandbox.base_url, keys, sandbox.metadata or {},
            )

    async def hover(
        self,
        *,
        user_id: str,
        session_id: str,
        selector: str | None = None,
        x: float | None = None,
        y: float | None = None,
    ) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.hover(
                sandbox.base_url,
                selector=selector, x=x, y=y, provider_metadata=sandbox.metadata or {},
            )

    async def scroll(
        self,
        *,
        user_id: str,
        session_id: str,
        direction: str | None = None,
        amount: int | None = None,
    ) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.scroll(
                sandbox.base_url,
                direction=direction, amount=amount, provider_metadata=sandbox.metadata or {},
            )

    async def evaluate(self, *, user_id: str, session_id: str, expression: str) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.evaluate(
                sandbox.base_url, expression, sandbox.metadata or {},
            )

    async def get_markdown(self, *, user_id: str, session_id: str) -> BrowserContentResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.get_markdown(
                sandbox.base_url, sandbox.metadata or {},
            )

    async def get_text(self, *, user_id: str, session_id: str) -> BrowserContentResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.get_text(
                sandbox.base_url, sandbox.metadata or {},
            )

    async def get_elements(self, *, user_id: str, session_id: str) -> BrowserListResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.get_elements(
                sandbox.base_url, sandbox.metadata or {},
            )

    async def list_tabs(self, *, user_id: str, session_id: str) -> BrowserListResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.list_tabs(
                sandbox.base_url, sandbox.metadata or {},
            )

    async def create_tab(self, *, user_id: str, session_id: str, url: str | None = None) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.create_tab(
                sandbox.base_url, url, sandbox.metadata or {},
            )

    async def close_tab(self, *, user_id: str, session_id: str, index: int) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.close_tab(
                sandbox.base_url, index, sandbox.metadata or {},
            )

    async def activate_tab(self, *, user_id: str, session_id: str, index: int) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.activate_tab(
                sandbox.base_url, index, sandbox.metadata or {},
            )

    async def get_cookies(self, *, user_id: str, session_id: str, urls: str | None = None) -> BrowserListResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.get_cookies(
                sandbox.base_url, urls=urls, provider_metadata=sandbox.metadata or {},
            )

    async def set_cookies(
        self, *, user_id: str, session_id: str, cookies: list[Mapping[str, Any]],
    ) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.set_cookies(
                sandbox.base_url, cookies, sandbox.metadata or {},
            )

    async def clear_cookies(self, *, user_id: str, session_id: str) -> BrowserActionResult:
        async with self._workspace_lease(user_id, session_id) as (_, sandbox):
            provider = self._providers.get(sandbox.provider_id)
            return await provider.clear_cookies(
                sandbox.base_url, sandbox.metadata or {},
            )

    @asynccontextmanager
    async def _workspace_lease(
        self,
        user_id: str,
        session_id: str,
    ) -> AsyncIterator[tuple[SessionWorkspaceDocument, SandboxDocument]]:
        workspace = await self._workspace_manager.allocate(user_id, session_id)
        sandbox = await self._sandboxes.get_by_id(workspace.sandbox_id)
        if sandbox is None or sandbox.state != SandboxState.USER_ACTIVE or sandbox.bind_user_id != user_id:
            raise ServiceException(SandboxErrorCode.SANDBOX_UNAVAILABLE)

        await self._sandboxes.register_active(
            sandbox.sandbox_id, user_id, datetime.now(timezone.utc),
        )

        try:
            yield workspace, sandbox
        finally:
            await self._sandboxes.unregister_active(sandbox.sandbox_id)

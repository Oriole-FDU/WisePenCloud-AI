from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any, BinaryIO, Protocol

from pydantic import BaseModel, Field


class BrowserViewport(BaseModel):
    """浏览器/页面视口尺寸"""

    width: int
    height: int


class BrowserInfoResult(BaseModel):
    """浏览器自描述信息"""

    user_agent: str
    cdp_url: str
    vnc_url: str | None = None
    cdp_ui_url: str | None = None
    viewport: BrowserViewport
    page_viewport: BrowserViewport | None = None


class BrowserActionResult(BaseModel):
    """浏览器动作/变更类接口的统一结果"""

    success: bool | None = None
    message: str | None = None
    hint: str | None = None
    data: Any | None = None


class BrowserContentResult(BaseModel):
    """页面内容读取结果（markdown/text/html）"""

    content: str = ""
    raw: Any | None = None


class BrowserListResult(BaseModel):
    """列表类读取结果（页面元素/标签页/cookie）"""

    items: list[Any] = Field(default_factory=list)
    raw: Any | None = None


class BrowserChannel(Protocol):
    """到沙箱浏览器 CDP 的双向字节/文本通道"""

    async def send(self, message: str | bytes) -> None: ...

    def __aiter__(self) -> AsyncIterator[str | bytes]: ...


class SandboxBrowserProvider(ABC):

    @abstractmethod
    async def get_info(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserInfoResult:
        pass

    @abstractmethod
    def connect_cdp(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> AbstractAsyncContextManager[BrowserChannel]:
        """建立到浏览器 CDP 的双向通道，退出时由实现负责关闭"""
        pass

    @abstractmethod
    async def screenshot(
        self,
        base_url: str,
        destination: BinaryIO,
        *,
        format: str = "png",
        quality: int | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> None:
        pass

    @abstractmethod
    async def execute_action(
        self,
        base_url: str,
        action: Mapping[str, Any],
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def navigate(
        self,
        base_url: str,
        url: str,
        *,
        wait_until: str | None = None,
        timeout: float | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def back(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def forward(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def reload(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def click(
        self,
        base_url: str,
        *,
        selector: str | None = None,
        index: int | None = None,
        x: float | None = None,
        y: float | None = None,
        button: str | None = None,
        click_count: int | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def fill(
        self,
        base_url: str,
        text: str,
        *,
        selector: str | None = None,
        index: int | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def press_key(
        self,
        base_url: str,
        key: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def hot_key(
        self,
        base_url: str,
        keys: list[str],
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def hover(
        self,
        base_url: str,
        *,
        selector: str | None = None,
        x: float | None = None,
        y: float | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def scroll(
        self,
        base_url: str,
        *,
        direction: str | None = None,
        amount: int | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def evaluate(
        self,
        base_url: str,
        expression: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def get_markdown(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserContentResult:
        pass

    @abstractmethod
    async def get_text(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserContentResult:
        pass

    @abstractmethod
    async def get_elements(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserListResult:
        pass

    @abstractmethod
    async def list_tabs(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserListResult:
        pass

    @abstractmethod
    async def create_tab(
        self,
        base_url: str,
        url: str | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def close_tab(
        self,
        base_url: str,
        index: int,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def activate_tab(
        self,
        base_url: str,
        index: int,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def get_cookies(
        self,
        base_url: str,
        *,
        urls: str | None = None,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserListResult:
        pass

    @abstractmethod
    async def set_cookies(
        self,
        base_url: str,
        cookies: list[Mapping[str, Any]],
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def clear_cookies(
        self,
        base_url: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def save_state(
        self,
        base_url: str,
        path: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

    @abstractmethod
    async def load_state(
        self,
        base_url: str,
        path: str,
        provider_metadata: Mapping[str, object] | None = None,
    ) -> BrowserActionResult:
        pass

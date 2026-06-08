import asyncio
import secrets
from typing import Any, Dict, List, Optional, Set, Tuple

from playwright.async_api import Browser, BrowserContext, Dialog, Page, async_playwright

from chat.application.tools.browser.services.browser_interact.enums import (
    BrowserDialogType,
    DiagnosticCode,
    RuntimeMode,
    ToolErrorCode,
)
from chat.application.tools.browser.services.browser_interact.errors import BrowserSessionError
from chat.application.tools.browser.services.browser_interact.models import (
    BrowserEvent,
    BrowserLaunchOptions,
)
from common.logger import log_error, log_fail

_SESSION_ID_BYTES = 12
_EVENT_BUFFER_SIZE = 20
_EVENT_MESSAGE_MAX_CHARS = 500


class BrowserSessionManager:
    def __init__(
        self,
        options: Optional[BrowserLaunchOptions] = None,
    ) -> None:
        """初始化单会话浏览器管理器。

        Args:
            options: 浏览器启动和运行时描述选项。默认使用本地 headed Chromium。
        """
        self._options = options or BrowserLaunchOptions()

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._session_id: Optional[str] = None
        self._lock = asyncio.Lock()
        self._console_events: List[BrowserEvent] = []
        self._page_error_events: List[BrowserEvent] = []
        self._dialog_events: List[BrowserEvent] = []
        self._network_events: List[BrowserEvent] = []
        self._main_document_status: Optional[int] = None
        self._observed_page_ids: Set[int] = set()
        self._dom_version_script_pages: Set[int] = set()

    @property
    def runtime_provider(self) -> str:
        return self._options.runtime_provider

    @property
    def runtime_engine(self) -> str:
        return self._options.runtime_engine

    @property
    def runtime_sandboxed(self) -> bool:
        return not self._options.disable_sandbox

    @property
    def runtime_mode(self) -> str:
        return RuntimeMode.HEADLESS.value if self._options.headless else RuntimeMode.HEADED.value

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @property
    def page(self) -> Optional[Page]:
        return self._page

    def set_current_page(self, page: Page) -> None:
        """更新当前活跃页面，用于 click 打开新页后的会话切换。"""
        self._page = page
        self._attach_page_observers(page)

    @property
    def has_session(self) -> bool:
        return self._session_id is not None

    @property
    def is_session_alive(self) -> bool:
        return self._page is not None and not self._page.is_closed()

    @property
    def open_pages_count(self) -> int:
        """返回当前 browser context 中仍打开的页面数量。"""
        if self._context is None:
            return 0
        return len([page for page in self._context.pages if not page.is_closed()])

    def browser_events_summary(self) -> Dict[str, object]:
        """返回低噪声浏览器事件摘要，供 status 诊断下一步策略。"""
        return {
            "open_pages_count": self.open_pages_count,
            "console_message_count": len(self._console_events),
            "page_error_count": len(self._page_error_events),
            "dialog_count": len(self._dialog_events),
            "latest_console_message": self._event_to_dict(self._console_events[-1])
            if self._console_events
            else None,
            "latest_page_error": self._event_to_dict(self._page_error_events[-1])
            if self._page_error_events
            else None,
            "latest_dialog": self._event_to_dict(self._dialog_events[-1])
            if self._dialog_events
            else None,
            "network_error_count": len(self._network_events),
            "latest_network_error": self._event_to_dict(self._network_events[-1])
            if self._network_events
            else None,
            "main_document_status": self._main_document_status,
        }

    async def current_dom_version(self) -> Optional[int]:
        """读取当前页面 DOM mutation 版本；页面不可用时返回 None。"""
        if self._page is None or self._page.is_closed():
            return None

        await self._ensure_dom_version_observer(self._page)
        try:
            version = await self._page.evaluate("() => window.__wisepenDomVersion || 0")
        except Exception:
            return None
        return version if isinstance(version, int) else None

    def clear_browser_events(self) -> None:
        """清空运行时事件缓冲，保留当前浏览器会话。"""
        self._console_events.clear()
        self._page_error_events.clear()
        self._dialog_events.clear()
        self._network_events.clear()

    async def list_tabs(self) -> List[Dict[str, Any]]:
        """返回当前 context 内所有打开 tab 的轻量状态。"""
        if self._context is None:
            return []

        tabs: List[Dict[str, Any]] = []
        for index, page in enumerate(self._context.pages):
            if page.is_closed():
                continue
            title = ""
            try:
                title = await page.title()
            except Exception:
                title = ""
            tabs.append(
                {
                    "index": index,
                    "current": page is self._page,
                    "url": page.url,
                    "title": title,
                }
            )
        return tabs

    async def new_tab(self, url: Optional[str] = None) -> Page:
        """创建新 tab，并可选导航到目标 URL。"""
        if self._context is None:
            raise BrowserSessionError(
                "Browser context is not available.",
                diagnostic_code=DiagnosticCode.BROWSER_SESSION_ERROR.value,
            )

        page = await self._context.new_page()
        self.set_current_page(page)
        if url:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return page

    async def switch_tab(self, tab_index: int) -> Optional[Page]:
        """按 context 页签索引切换当前页面。"""
        if self._context is None:
            return None

        pages = self._context.pages
        if tab_index < 0 or tab_index >= len(pages):
            return None

        page = pages[tab_index]
        if page.is_closed():
            return None

        self.set_current_page(page)
        try:
            await page.bring_to_front()
        except Exception:
            pass
        return page

    async def close_tab(self, tab_index: Optional[int] = None) -> Optional[Page]:
        """关闭指定 tab；未指定时关闭当前 tab 并切换到剩余最后一个页面。"""
        if self._context is None:
            return None

        pages = [page for page in self._context.pages if not page.is_closed()]
        if not pages:
            return None

        target = self._page if tab_index is None else None
        if tab_index is not None:
            all_pages = self._context.pages
            if tab_index < 0 or tab_index >= len(all_pages):
                return None
            target = all_pages[tab_index]

        if target is None or target.is_closed():
            return None

        await target.close()
        remaining = [page for page in self._context.pages if not page.is_closed()]
        self._page = remaining[-1] if remaining else None
        if self._page is not None:
            self._attach_page_observers(self._page)
        return self._page

    async def cleanup(self) -> None:
        """关闭浏览器相关资源。"""
        await self._close()

    async def _close(self) -> None:
        if self._page is not None:
            try:
                await self._page.close()
            except Exception:
                log_fail("关闭 page，可能存在资源泄漏", "")
            self._page = None

        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                log_fail("关闭 browser context，可能存在资源泄漏", "")
            self._context = None

        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                log_fail("关闭 browser，可能存在资源泄漏", "")
            self._browser = None

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                log_fail("停止 Playwright，可能存在资源泄漏", "")
            self._playwright = None

        self._session_id = None
        self._console_events.clear()
        self._page_error_events.clear()
        self._dialog_events.clear()
        self._network_events.clear()
        self._main_document_status = None
        self._observed_page_ids.clear()
        self._dom_version_script_pages.clear()

    async def validate_session(
        self,
        browser_session_id: Optional[str],
    ) -> Optional[ToolErrorCode]:
        """校验调用方传入的 browser_session_id 是否匹配当前活跃会话。"""
        if browser_session_id is None:
            return ToolErrorCode.SESSION_REQUIRED

        if self._session_id is None:
            return ToolErrorCode.SESSION_NOT_FOUND

        if browser_session_id != self._session_id:
            return ToolErrorCode.SESSION_MISMATCH

        if not self.is_session_alive:
            return ToolErrorCode.SESSION_EXPIRED

        return None

    async def get_existing_page(
        self,
        browser_session_id: Optional[str],
    ) -> Tuple[Optional[Page], Optional[ToolErrorCode]]:
        """获取当前页面；不会创建新浏览器。"""
        browser_session_id = browser_session_id or None
        error_code = await self.validate_session(browser_session_id)
        if error_code is not None:
            return None, error_code

        return self._page, None

    async def get_or_create_page(
        self,
        browser_session_id: Optional[str],
    ) -> Tuple[Optional[Page], Optional[ToolErrorCode]]:
        """获取当前页面；没有活跃页面时启动新的浏览器会话。"""
        browser_session_id = browser_session_id or None

        if browser_session_id is not None:
            error_code = await self.validate_session(browser_session_id)
            if error_code is not None:
                return None, error_code
            return self._page, None

        async with self._lock:
            if self._page is not None and not self._page.is_closed():
                return self._page, None

            try:
                if self._playwright is None:
                    self._playwright = await async_playwright().start()

                launch_args = []
                if self._options.disable_sandbox:
                    launch_args.append("--no-sandbox")
                if self._options.disable_dev_shm_usage:
                    launch_args.append("--disable-dev-shm-usage")

                launch_kwargs = {
                    "headless": self._options.headless,
                    "timeout": self._options.timeout * 1000,
                }

                if launch_args:
                    launch_kwargs["args"] = launch_args

                self._browser = await self._playwright.chromium.launch(**launch_kwargs)
                self._context = await self._browser.new_context()
                self._page = await self._context.new_page()
                self._attach_page_observers(self._page)
                self._session_id = secrets.token_hex(_SESSION_ID_BYTES)

                return self._page, None

            except Exception as e:
                diagnostic_code = self._classify_launch_error(e)
                log_error(
                    "浏览器启动",
                    str(e),
                    diagnostic_code=diagnostic_code,
                )
                await self._close()
                raise BrowserSessionError(
                    f"Failed to create browser session: {e}",
                    diagnostic_code=diagnostic_code,
                ) from e

    @staticmethod
    def _classify_launch_error(e: Exception) -> str:
        """将 Playwright 启动异常归类为稳定诊断码。"""
        message = str(e).lower()
        if (
            "no display" in message
            or "missing x server" in message
            or "xvfb" in message
        ):
            return DiagnosticCode.NO_DISPLAY_SERVER.value
        if "target page, context or browser has been closed" in message:
            return DiagnosticCode.BROWSER_CRASHED_ON_START.value
        if "timeout" in message:
            return DiagnosticCode.LAUNCH_TIMEOUT.value
        return DiagnosticCode.BROWSER_LAUNCH_FAILED.value

    def _attach_page_observers(self, page: Page) -> None:
        """为页面注册运行时观测回调，避免 action 因 dialog 长时间挂起。"""
        page_id = id(page)
        if page_id in self._observed_page_ids:
            return

        self._observed_page_ids.add(page_id)
        self._dom_version_script_pages.discard(page_id)
        page.on("console", lambda message: self._record_console_event(page, message))
        page.on("pageerror", lambda error: self._record_page_error_event(page, error))
        page.on("requestfailed", lambda request: self._record_request_failed(page, request))
        page.on("response", lambda response: self._record_response(page, response))
        page.on(
            "dialog",
            lambda dialog: asyncio.create_task(self._handle_dialog(page, dialog)),
        )

    def _record_console_event(self, page: Page, message) -> None:
        """记录最近的 console 事件，status 只暴露摘要。"""
        try:
            text = f"{message.type}: {message.text}"
        except Exception:
            text = "console event"
        self._append_event(self._console_events, BrowserEvent("console", text, page.url))

    def _record_page_error_event(self, page: Page, error: Exception) -> None:
        """记录最近的 pageerror，帮助上层判断页面脚本是否异常。"""
        self._append_event(
            self._page_error_events,
            BrowserEvent("pageerror", str(error), page.url),
        )

    def _record_request_failed(self, page: Page, request) -> None:
        """记录最近失败请求，帮助诊断空白页或资源加载失败。"""
        try:
            failure = request.failure or {}
            error_text = failure.get("errorText", "") if isinstance(failure, dict) else ""
            message = f"{request.method} {self._redact_url(request.url)} {error_text}".strip()
        except Exception:
            message = "request failed"
        self._append_event(
            self._network_events,
            BrowserEvent("requestfailed", message, page.url),
        )

    def _record_response(self, page: Page, response) -> None:
        """记录主文档状态码和最近 4xx/5xx 响应。"""
        try:
            request = response.request
            status = int(response.status)
            url = self._redact_url(response.url)
            if request.resource_type == "document" and response.frame == page.main_frame:
                self._main_document_status = status
            if status < 400:
                return
            message = f"{status} {request.method} {url}"
        except Exception:
            return
        self._append_event(
            self._network_events,
            BrowserEvent("http_error", message, page.url),
        )

    async def _handle_dialog(self, page: Page, dialog: Dialog) -> None:
        """处理浏览器 dialog，避免 alert/confirm/prompt 阻塞后续动作。"""
        dialog_type = dialog.type
        event = BrowserEvent(
            type=f"dialog:{dialog_type}",
            message=dialog.message,
            page_url=page.url,
        )
        self._append_event(self._dialog_events, event)

        try:
            if dialog_type in (
                BrowserDialogType.ALERT.value,
                BrowserDialogType.BEFORE_UNLOAD.value,
            ):
                await dialog.accept()
            else:
                await dialog.dismiss()
        except Exception as error:
            log_fail("处理浏览器 dialog", str(error))

    def _append_event(self, buffer: List[BrowserEvent], event: BrowserEvent) -> None:
        """向固定长度事件缓冲区追加一条已脱敏长度的事件。"""
        buffer.append(
            BrowserEvent(
                type=event.type,
                message=event.message[:_EVENT_MESSAGE_MAX_CHARS],
                page_url=event.page_url,
            )
        )
        del buffer[:-_EVENT_BUFFER_SIZE]

    @staticmethod
    def _event_to_dict(event: BrowserEvent) -> Dict[str, Optional[str]]:
        """把事件 dataclass 转换为 response detail 可序列化结构。"""
        return {
            "type": event.type,
            "message": event.message,
            "page_url": event.page_url,
        }

    @staticmethod
    def _redact_url(url: str) -> str:
        """保留 URL 来源与路径，去掉 query/fragment 中可能出现的敏感参数。"""
        if "?" in url:
            url = url.split("?", 1)[0] + "?***"
        if "#" in url:
            url = url.split("#", 1)[0] + "#***"
        return url

    async def _ensure_dom_version_observer(self, page: Page) -> None:
        """在页面内安装轻量 MutationObserver，记录 DOM 变化版本。"""
        page_id = id(page)
        if page_id in self._dom_version_script_pages:
            return

        try:
            await page.evaluate(
                """() => {
                    if (window.__wisepenDomVersionObserverInstalled) return;
                    window.__wisepenDomVersion = 0;
                    window.__wisepenDomVersionObserverInstalled = true;
                    const observer = new MutationObserver(() => {
                        window.__wisepenDomVersion = (window.__wisepenDomVersion || 0) + 1;
                    });
                    observer.observe(document.documentElement || document, {
                        childList: true,
                        subtree: true,
                        attributes: true,
                        characterData: true
                    });
                }"""
            )
            self._dom_version_script_pages.add(page_id)
        except Exception:
            pass

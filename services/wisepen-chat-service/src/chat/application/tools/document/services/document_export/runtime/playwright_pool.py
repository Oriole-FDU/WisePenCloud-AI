import asyncio
from pathlib import Path
from typing import Optional, Tuple

from playwright.async_api import Browser, Playwright, async_playwright

from chat.application.tools.document.services.document_export.errors import ExportRenderError
from common.logger import log_error, log_event


class BrowserPoolUnavailableError(Exception):
    """浏览器池不可用异常，携带诊断码便于排查。"""
    def __init__(
        self,
        message: str,
        *,
        diagnostic_code: str = "BROWSER_POOL_UNAVAILABLE",
    ):
        """初始化异常，设置诊断码用于分类处理。"""
        self.diagnostic_code = diagnostic_code
        super().__init__(message)


class PlaywrightBrowserPool:
    """
    Playwright Chromium 浏览器池。

    - 进程级复用一个 Browser 实例。
    - 每次 PDF 渲染创建独立 BrowserContext。
    - 通过 semaphore 限制同时渲染的 context 数量。
    - 浏览器异常后自动重启一次，避免单次崩溃污染后续导出。
    """

    def __init__(self, *, max_contexts: int = 8):
        """初始化 PlaywrightBrowserPool，设置最大并发 context 数。"""
        self.max_contexts = max_contexts
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._browser_generation = 0
        self._semaphore = asyncio.Semaphore(self.max_contexts)
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """启动浏览器池，提前暴露 Chromium 缺失/运行环境问题。"""
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return

            await self._stop_locked()
            await self._start_locked()

    async def stop(self) -> None:
        """停止浏览器池，释放浏览器与 Playwright 进程资源。"""
        async with self._lock:
            await self._stop_locked()

    async def _start_locked(self) -> None:
        """在锁保护下启动 Playwright 和 Chromium 浏览器。"""
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            self._browser_generation += 1

            log_event(
                "DocumentExport BrowserPool 启动",
                generation=self._browser_generation,
                max_contexts=self.max_contexts,
            )

        except Exception as e:
            diagnostic_code = self._classify_browser_error(e)
            log_error(
                "DocumentExport BrowserPool 启动失败",
                e,
                diagnostic_code=diagnostic_code,
            )
            await self._stop_locked()
            raise BrowserPoolUnavailableError(
                self._format_browser_error(e, diagnostic_code=diagnostic_code),
                diagnostic_code=diagnostic_code,
            ) from e

    async def _stop_locked(self) -> None:
        """在锁保护下关闭浏览器和 Playwright 实例。"""
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        log_event("DocumentExport BrowserPool 停止")

    async def _get_browser_snapshot(self) -> Tuple[Browser, int]:
        """获取当前可用 browser 实例及其 generation，失败时自动重启。"""
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                await self._stop_locked()
                await self._start_locked()

            if self._browser is None:
                raise BrowserPoolUnavailableError(
                    "Playwright browser is not available."
                )

            return self._browser, self._browser_generation

    async def _restart_browser_if_current(self, failed_generation: int) -> None:
        """如果当前 browser 仍是失败的 generation，则重启浏览器实例。"""
        async with self._lock:
            if (
                self._browser is not None
                and self._browser.is_connected()
                and self._browser_generation != failed_generation
            ):
                return

            await self._stop_locked()
            await self._start_locked()

    async def render_pdf_from_html(
        self,
        *,
        html: str,
        output_path: Path,
        timeout_seconds: float = 60.0,
    ) -> None:
        """将 HTML 渲染为 PDF，失败后自动重启浏览器重试一次。"""
        last_error: Optional[BaseException] = None

        # 渲染失败时重启浏览器后重试一次。
        for attempt in range(2):
            browser, generation = await self._get_browser_snapshot()

            try:
                async with self._semaphore:
                    await asyncio.wait_for(
                        self._render_with_context(
                            browser=browser,
                            html=html,
                            output_path=output_path,
                            timeout_seconds=timeout_seconds,
                        ),
                        timeout=timeout_seconds,
                    )
                return

            except asyncio.TimeoutError as e:
                raise ExportRenderError("PDF rendering timed out.") from e

            except Exception as e:
                if isinstance(e, (BrowserPoolUnavailableError, ExportRenderError)):
                    raise

                last_error = e

                if attempt >= 1:
                    diagnostic_code = self._classify_browser_error(e)
                    log_error(
                        "DocumentExport PDF 渲染失败",
                        e,
                        diagnostic_code=diagnostic_code,
                        attempt=attempt + 1,
                    )
                    raise ExportRenderError(
                        self._format_browser_error(
                            e,
                            diagnostic_code=diagnostic_code,
                            prefix="Failed to render PDF",
                        )
                    ) from e

                await self._restart_browser_if_current(generation)

        if last_error is not None:
            raise last_error

    async def _render_with_context(
        self,
        *,
        browser: Browser,
        html: str,
        output_path: Path,
        timeout_seconds: float,
    ) -> None:
        """在独立 BrowserContext 中渲染 HTML 为 PDF，阻断外部网络请求。"""
        context = await browser.new_context(java_script_enabled=False)
        page = None

        async def block_external_requests(self, route) -> None:
            """阻断外部网络请求，仅允许 data: 和 about:blank 协议。"""
            request_url = route.request.url
            if request_url.startswith("data:") or request_url == "about:blank":
                await route.continue_()
                return

            await route.abort()

        try:
            await context.route("**/*", block_external_requests)
            page = await context.new_page()

            await page.set_content(
                html,
                wait_until="load",
                timeout=timeout_seconds * 1000,
            )
            await page.pdf(
                path=str(output_path),
                print_background=True,
                prefer_css_page_size=True,
            )

        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

            try:
                await context.close()
            except Exception:
                pass

    @staticmethod
    def _classify_browser_error(e: BaseException) -> str:
        """将浏览器异常分类为可读的诊断码。"""
        message = str(e).lower()

        if (
            "executable doesn't exist" in message
            or "executable not found" in message
            or "please run the following command" in message
            or "playwright install" in message
        ):
            return "PLAYWRIGHT_BROWSER_MISSING"

        if (
            "running as root without --no-sandbox" in message
            or "no usable sandbox" in message
            or "setuid sandbox" in message
        ):
            return "PLAYWRIGHT_SANDBOX_UNAVAILABLE"

        if "target page, context or browser has been closed" in message:
            return "PLAYWRIGHT_BROWSER_CLOSED"

        if "timeout" in message:
            return "PLAYWRIGHT_TIMEOUT"

        return "PLAYWRIGHT_RENDER_FAILED"

    @staticmethod
    def _format_browser_error(
        e: BaseException,
        *,
        diagnostic_code: str,
        prefix: str = "PDF browser unavailable",
    ) -> str:
        """将浏览器异常格式化为友好的错误消息。"""
        detail = str(e).strip().splitlines()[0] if str(e).strip() else repr(e)

        if diagnostic_code == "PLAYWRIGHT_BROWSER_MISSING":
            return (
                f"{prefix}: Playwright Chromium is not installed. "
                "Run `python -m playwright install chromium` in the runtime image."
            )

        if diagnostic_code == "PLAYWRIGHT_SANDBOX_UNAVAILABLE":
            return (
                f"{prefix}: Chromium sandbox is unavailable. "
                "Run with a supported Chromium sandbox."
            )

        if diagnostic_code == "PLAYWRIGHT_TIMEOUT":
            return f"{prefix}: browser operation timed out."

        return f"{prefix}: {detail}"

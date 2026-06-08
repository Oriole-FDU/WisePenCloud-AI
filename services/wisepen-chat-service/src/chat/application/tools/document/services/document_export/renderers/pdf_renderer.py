from chat.application.tools.document.services.document_export.enums import ExportFormat
from chat.application.tools.document.services.document_export.errors import ExportRenderError
from chat.application.tools.document.services.document_export.models import ExportRequest
from chat.application.tools.document.services.document_export.renderers.base import (
    DocumentRenderer,
)
from chat.application.tools.document.services.document_export.renderers.html_renderer import (
    HtmlRenderer,
)
from chat.application.tools.document.services.document_export.runtime.playwright_pool import (
    BrowserPoolUnavailableError,
    PlaywrightBrowserPool,
)


class PdfRenderer(DocumentRenderer):
    """
    Markdown -> PDF 渲染器。

    - 先用 HtmlRenderer 通过 Pandoc 生成完整 HTML。
    - 再交给 PlaywrightBrowserPool 使用 Chromium 打印为 PDF。
    """

    def __init__(
        self,
        *,
        html_renderer: HtmlRenderer,
        browser_pool: PlaywrightBrowserPool,
    ) -> None:
        """初始化对象依赖。"""
        self.html_renderer = html_renderer
        self.browser_pool = browser_pool

    @property
    def target_format(self) -> ExportFormat:
        return ExportFormat.PDF

    async def render(self, request: ExportRequest) -> None:
        html = await self.html_renderer.render_to_string(
            request.markdown,
            title=request.options.title,
            css=request.options.css,
            assets_dir=request.options.assets_dir,
            timeout_seconds=request.options.timeout_seconds,
        )

        try:
            await self.browser_pool.render_pdf_from_html(
                html=html,
                output_path=request.output_path,
                timeout_seconds=request.options.timeout_seconds,
            )

        except BrowserPoolUnavailableError as e:
            raise ExportRenderError(f"Failed to render PDF: {e}") from e

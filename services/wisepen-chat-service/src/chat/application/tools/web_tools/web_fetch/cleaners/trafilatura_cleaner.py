from __future__ import annotations

from chat.application.tools.utils.markdown_renderer.html_renderer import WebPageMarkdownRenderer
from common.logger import warn
from .base import CleanedOutput


class TrafilaturaCleaner:
    """trafilatura 清洗器。

    复用仓库现有 WebPageMarkdownRenderer（trafilatura 封装）进行正文抽取。
    同步实现（trafilatura 本身是同步库），返回 CleanedOutput。

    注意：WebPageMarkdownRenderer.render() 返回 str | None（纯 Markdown 正文），
    不提供 title 结构化字段，故 title 在 trafilatura 路径下为 None。
    """

    __slots__ = ("_renderer",)

    def __init__(self, renderer: WebPageMarkdownRenderer) -> None:
        self._renderer = renderer

    @property
    def name(self) -> str:
        return "trafilatura"

    def clean(self, raw_html: str, *, url: str | None = None) -> CleanedOutput:
        if not raw_html or not raw_html.strip():
            return CleanedOutput(markdown=None, cleaner=self.name)

        try:
            markdown = self._renderer.render(raw_html, url=url)
        except Exception as exc:  # noqa: BLE001 - trafilatura 异常统一降级为空结果
            warn("web_fetch trafilatura clean failed", url=url, error=str(exc))
            return CleanedOutput(markdown=None, cleaner=self.name)

        return CleanedOutput(
            markdown=markdown,
            cleaner=self.name,
            title=None,
        )

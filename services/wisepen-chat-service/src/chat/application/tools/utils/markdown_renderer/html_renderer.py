from __future__ import annotations

import re

import trafilatura
from bs4 import BeautifulSoup
from markdownify import ATX, markdownify as markdownify_html


class FragmentMarkdownRenderer:
    """HTML 片段 Markdown 渲染器（直接进行结构树平铺渲染）。"""

    def __init__(self, *, remove_noise_tags: bool = True) -> None:
        self._remove_noise_tags = remove_noise_tags

    def render(self, html: str) -> str | None:
        stripped = html.strip()
        if not stripped:
            return None

        try:
            cleaned_html = (
                self._remove_noise_tags_from_html(stripped)
                if self._remove_noise_tags
                else stripped
            )

            rendered = markdownify_html(
                cleaned_html,
                heading_style=ATX,
                bullets="-",
                autolinks=False,
                default_title=False,
                table_infer_header=True,
                escape_asterisks=False,
                escape_underscores=False,
            )
        except Exception:
            return None

        return _normalize_markdown(rendered)

    @staticmethod
    def _remove_noise_tags_from_html(html: str) -> str:
        """从 HTML 中剔除不适合进入大模型或知识库的噪声标签。"""
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup.select("script, style, noscript, template, svg, canvas"):
            tag.decompose()

        return str(soup)


class WebPageMarkdownRenderer:
    """网页核心正文 Markdown 抽取器（基于密度算法，只提取网页的主体文本）。"""

    def render(self, html: str, *, url: str | None = None) -> str | None:
        stripped = html.strip()
        if not stripped:
            return None

        try:
            extracted = trafilatura.extract(
                stripped,
                url=url,
                output_format="markdown",
                include_comments=False,
                include_tables=True,
                include_links=True,
                favor_precision=False,
                favor_recall=True,
            )
        except Exception:
            return None

        return _normalize_markdown(extracted)


def _normalize_markdown(markdown: str | None) -> str | None:
    """规范化换行符，并压缩多余的连续空行。"""
    if not markdown:
        return None

    # 跨平台兼顾：干净地切分并重新以 \n 组装，天然统一 \r, \r\n, \n
    text = "\n".join(markdown.splitlines()).strip()

    # 将连续 3 个及以上的换行符压缩为标准的双换行（段落分隔）
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text or None

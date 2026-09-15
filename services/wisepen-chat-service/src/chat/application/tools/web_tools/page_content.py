from __future__ import annotations

import asyncio
import re

import pdf_inspector
import trafilatura
from common.logger import warn

from .fetchers.base import RawFetchOutput
from .fetchers import UrlFetchError

# 移除不可读页面或噪声页面
PRUNE_XPATH = (
    "//script",
    "//style",
    "//noscript",
    "//template",
    "//svg",
    "//canvas",
    "//iframe",
    "//header",
    "//nav",
    "//footer",
    "//aside",
    "//form",
    "//button",
    "//*[translate(@aria-hidden, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='true']",
    "//*[@hidden]",
    "//*[@inert]",
    "//*[@data-animated-cell]",
)
# 常见的拦截提示语
_CONTENT_BLOCKED_RE = re.compile(
    r"\b(?:access\s+denied|enable\s+javascript(?:\s+and\s+cookies)?|"
    r"checking\s+your\s+browser|are\s+you\s+a\s+robot|verify\s+you\s+are\s+human|"
    r"complete\s+a\s+captcha)\b",
    re.IGNORECASE,
)
# 常见的 HTML 反爬指纹
_HTML_FINGERPRINT_RE = re.compile(
    r"cf-mitigated|cf-ray|__cf_bm|_cf_chl|datadome|dd_siteid|"
    r"challenge-form|challenge-running|challenge-error|px-captcha|px-block-page|"
    r"_pxAppId|incapsula|visitorId.*incap|akamai-challenge|aka_browser_check",
    re.IGNORECASE,
)


def clean_html(raw_html: str, *, url: str | None = None) -> str | None:
    if not raw_html or not raw_html.strip():
        return None
    try:
        markdown = trafilatura.extract(
            raw_html.strip(),
            url=url,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            include_links=True,
            favor_precision=False,
            favor_recall=True,
            prune_xpath=list(PRUNE_XPATH),
        )
    except Exception as exc:  # noqa: BLE001 - 第三方清洗失败按低质量结果处理
        warn("web fetch HTML 清洗失败", url=url, error=str(exc))
        return None
    if not markdown:
        return None
    return re.sub(r"\n{3,}", "\n\n", markdown.strip()) or None


def should_fallback(
    *,
    raw: RawFetchOutput,
    markdown: str | None,
    min_text_length: int = 200,
) -> bool:
    """判断静态抓取结果是否需要浏览器阶段。"""
    if not raw.raw_html or not raw.raw_html.strip():
        return True
    if not markdown or not markdown.strip() or len(markdown.strip()) < min_text_length:
        return True
    return bool(
        _CONTENT_BLOCKED_RE.search(markdown[:2000])
        or _HTML_FINGERPRINT_RE.search(raw.raw_html[:8000])
    )


async def extract_pdf_markdown(content: bytes, *, url: str) -> str:
    """使用 PDF 原生文本层提取，document extract 不在本次迁移范围内。"""
    try:
        result = await asyncio.to_thread(
            pdf_inspector.extract_pages_markdown_bytes,
            content,
        )
    except Exception as exc:
        raise UrlFetchError(url=url, reason=f"PDF extraction failed: {exc}") from exc

    pages = [
        f"<!-- page {page.page + 1} -->\n\n{page.markdown.strip()}"
        for page in result.pages
        if page.markdown.strip()
    ]
    if not pages:
        raise UrlFetchError(url=url, reason="PDF contains no extractable markdown")
    return "\n\n".join(pages)

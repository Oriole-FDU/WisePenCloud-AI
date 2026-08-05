from __future__ import annotations

import asyncio
import re

import pdf_inspector
import trafilatura

from common.logger import warn

from .core.errors import UrlFetchError
from .core.models import RawFetchOutput


# --- HTML 正文清洗 ---

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
    "//*[translate(@aria-hidden, "
    "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
    "'abcdefghijklmnopqrstuvwxyz')='true']",
    "//*[@hidden]",
    "//*[@inert]",
    "//*[@data-animated-cell]",
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
    except Exception as exc:
        warn(
            "web_fetch trafilatura clean failed",
            url=url,
            error=str(exc),
        )
        return None

    if not markdown:
        return None

    return re.sub(r"\n{3,}", "\n\n", markdown.strip()) or None


# --- 正文可用性判断 ---

# 正文语义关键词，带词界避免误判。
_CONTENT_BLOCKED_RE = re.compile(
    r"\b(?:"
    r"access\s+denied"
    r"|enable\s+javascript(?:\s+and\s+cookies)?"
    r"|checking\s+your\s+browser"
    r"|are\s+you\s+a\s+robot"
    r"|verify\s+you\s+are\s+human"
    r"|complete\s+a\s+captcha"
    r")\b",
    re.IGNORECASE,
)

# 原始 HTML 指纹，用于识别常见反爬挑战页。
_HTML_FINGERPRINT_RE = re.compile(
    r"cf-mitigated|cf-ray|__cf_bm|_cf_chl"
    r"|datadome|dd_siteid"
    r"|challenge-form|challenge-running|challenge-error"
    r"|px-captcha|px-block-page|_pxAppId"
    r"|incapsula|visitorId.*incap"
    r"|akamai-challenge|aka_browser_check",
    re.IGNORECASE,
)

_FINGERPRINT_SAMPLE_BYTES = 8000
_CONTENT_SAMPLE_CHARS = 2000


def should_fallback(
    *,
    raw: RawFetchOutput,
    markdown: str | None,
    min_text_length: int = 200,
) -> bool:
    """判断抓取结果是否满足正文消费要求。"""
    if not raw.raw_html or not raw.raw_html.strip():
        return True

    if not markdown or not markdown.strip():
        return True

    if len(markdown.strip()) < min_text_length:
        return True

    blocked_by_content = _CONTENT_BLOCKED_RE.search(
        markdown[:_CONTENT_SAMPLE_CHARS]
    )

    blocked_by_fingerprint = _HTML_FINGERPRINT_RE.search(
        raw.raw_html[:_FINGERPRINT_SAMPLE_BYTES]
    )

    return bool(blocked_by_content or blocked_by_fingerprint)


# --- PDF 正文提取 ---

async def extract_pdf_markdown(
    content: bytes,
    *,
    url: str,
) -> str:
    """将原生文本 PDF 快速转换为带页码标记的 Markdown。"""
    try:
        result = await asyncio.to_thread(
            pdf_inspector.extract_pages_markdown_bytes,
            content,
        )
    except Exception as exc:
        raise UrlFetchError(
            url=url,
            reason=f"PDF extraction failed: {exc}",
        ) from exc

    pages = [
        (
            f"<!-- page {page.page + 1} -->\n\n"
            f"{page.markdown.strip()}"
        )
        for page in result.pages
        if page.markdown.strip()
    ]

    if not pages:
        raise UrlFetchError(
            url=url,
            reason="PDF contains no extractable markdown",
        )

    return "\n\n".join(pages)

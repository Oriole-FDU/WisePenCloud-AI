from __future__ import annotations

import re

from ..core.models import RawFetchOutput


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

from __future__ import annotations

import re

from ..cleaners.base import CleanedOutput
from ..fetchers.base import RawFetchOutput
from ..models import FetchQuality

# 正文语义关键词，带词界避免误判
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

# 原始 HTML 指纹（命中已知反爬服务类名/script 注入）
_HTML_FINGERPRINT_RE = re.compile(
    r"cf-mitigated|cf-ray|__cf_bm|_cf_chl"
    r"|datadome|dd_siteid"
    r"|challenge-form|challenge-running|challenge-error"
    r"|px-captcha|px-block-page|_pxAppId"
    r"|incapsula|visitorId.*incap"
    r"|akamai-challenge|aka_browser_check",
    re.IGNORECASE,
)


def judge_quality(
    *,
    raw: RawFetchOutput,
    cleaned: CleanedOutput,
    min_text_length: int = 200,
) -> FetchQuality:
    # 1. raw_html 为空
    if not raw.raw_html or not raw.raw_html.strip():
        return FetchQuality(
            usable=False, should_fallback=True,
            reason="empty_html", text_length=0,
        )

    # 2. 清洗后正文为空
    markdown = cleaned.markdown or ""
    if not markdown.strip():
        return FetchQuality(
            usable=False, should_fallback=True,
            reason="empty_content", text_length=0,
        )

    # 3. 正文过短
    text_length = len(markdown.strip())
    if text_length < min_text_length:
        return FetchQuality(
            usable=False, should_fallback=True,
            reason="content_too_short", text_length=text_length,
        )

    # 4. 疑似反爬页面（两层独立判断，任一命中即触发）
    content_sample = f"{cleaned.title or ''} {markdown[:2000]}"
    blocked_by_content = _CONTENT_BLOCKED_RE.search(content_sample) is not None
    blocked_by_fingerprint = _HTML_FINGERPRINT_RE.search(raw.raw_html[:8000]) is not None

    if blocked_by_content or blocked_by_fingerprint:
        reason = (
            "blocked_page:fingerprint" if blocked_by_fingerprint
            else "blocked_page:content"
        )
        return FetchQuality(
            usable=False, should_fallback=True,
            reason=reason, text_length=text_length,
        )

    # 5. 正常
    return FetchQuality(
        usable=True, should_fallback=False,
        reason="ok", text_length=text_length,
    )

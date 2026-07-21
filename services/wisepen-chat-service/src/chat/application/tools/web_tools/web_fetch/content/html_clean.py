from __future__ import annotations

import re

import trafilatura

from common.logger import warn


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

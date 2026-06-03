from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin

from chat.application.tools.web.services.web_fetch.models import FetchedLink
from chat.application.tools.web.utils.html import clean_inline_html_text, html_unescape_url
from chat.application.tools.web.utils.urls import canonicalize_url

_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]{1,200})\]\(([^)\s]+)\)")
_BARE_URL_RE = re.compile(r"https?://[^\s<>)\]]+")
_HTML_HREF_RE = re.compile(
    r"""<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'<>`]+))[^>]*>(.*?)</a\s*>""",
    re.IGNORECASE | re.DOTALL,
)

_MAX_EXTRACTED_LINKS = 500


@dataclass(frozen=True, slots=True)
class ExtractedLink:
    url: str
    anchor_text: str
    surrounding_text: str
    source: str = "markdown"


class LinkExtractor:

    @staticmethod
    def merge(
        *,
        markdown: str,
        base_url: str,
        fetched_links: Optional[Iterable[FetchedLink]] = None,
    ) -> List[ExtractedLink]:
        links: List[ExtractedLink] = []
        seen: Set[str] = set()

        for source_links in (
            (
                ExtractedLink(
                    url=link.url,
                    anchor_text=link.anchor_text,
                    surrounding_text=link.surrounding_text,
                    source="dom",
                )
                for link in fetched_links or []
            ),
            LinkExtractor._extract_markdown_links(markdown),
            LinkExtractor._extract_html_anchor_links(markdown, base_url=base_url),
        ):
            for link in source_links:
                key = canonicalize_url(link.url, base_url=base_url).rstrip("/")
                if key in seen:
                    continue

                seen.add(key)
                links.append(link)

        return links[:_MAX_EXTRACTED_LINKS]

    @staticmethod
    def _extract_markdown_links(
        markdown: str,
        *,
        context_chars: int = 240,
    ) -> List[ExtractedLink]:
        links: List[ExtractedLink] = []
        occupied_ranges: List[Tuple[int, int]] = []

        for match in _MARKDOWN_LINK_RE.finditer(markdown):
            anchor = match.group(1).strip()
            url = match.group(2).strip()
            context = LinkExtractor._extract_context(markdown, match.start(), match.end(), context_chars)

            links.append(
                ExtractedLink(
                    url=url,
                    anchor_text=anchor,
                    surrounding_text=context,
                    source="markdown_link",
                )
            )
            occupied_ranges.append(match.span())

        for match in _BARE_URL_RE.finditer(markdown):
            if any(start <= match.start() < end for start, end in occupied_ranges):
                continue

            url = match.group(0).rstrip(".,;:")
            context = LinkExtractor._extract_context(markdown, match.start(), match.end(), context_chars)

            links.append(
                ExtractedLink(
                    url=url,
                    anchor_text="",
                    surrounding_text=context,
                    source="bare_url",
                )
            )

        return links

    @staticmethod
    def _extract_html_anchor_links(
        html_or_markdown: str,
        *,
        base_url: str,
        context_chars: int = 240,
    ) -> List[ExtractedLink]:
        links: List[ExtractedLink] = []

        for match in _HTML_HREF_RE.finditer(html_or_markdown):
            href = (match.group(1) or match.group(2) or match.group(3) or "").strip()
            if not href:
                continue

            anchor_html = match.group(4) or ""
            anchor = clean_inline_html_text(anchor_html)
            context = LinkExtractor._extract_context(
                html_or_markdown,
                match.start(),
                match.end(),
                context_chars,
            )

            links.append(
                ExtractedLink(
                    url=urljoin(base_url, html_unescape_url(href)),
                    anchor_text=anchor,
                    surrounding_text=clean_inline_html_text(context),
                    source="html_anchor",
                )
            )

        return links

    @staticmethod
    def _extract_context(text: str, start: int, end: int, context_chars: int) -> str:
        left = max(0, start - context_chars)
        right = min(len(text), end + context_chars)
        context = text[left:right]
        return re.sub(r"\s+", " ", context).strip()
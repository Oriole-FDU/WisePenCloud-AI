from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RawFetchOutput:
    source_url: str
    headers: dict[str, str] = field(default_factory=dict)
    raw_html: str | None = None
    pdf_bytes: bytes | None = None


@dataclass(frozen=True, slots=True)
class WebFetchResult:
    source_url: str
    text: str
    is_md: bool

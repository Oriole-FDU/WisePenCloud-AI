from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


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


class WebContentCacheMode(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


@dataclass(frozen=True, slots=True)
class WebContentCacheValue:
    user_id: str
    canonical_url: str
    cache_mode: WebContentCacheMode
    text: str
    is_md: bool
    expire_at: datetime
    raw_html: str | None = None

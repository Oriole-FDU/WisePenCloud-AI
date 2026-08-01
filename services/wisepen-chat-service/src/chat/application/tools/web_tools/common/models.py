from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
@dataclass(frozen=True, slots=True)
class WebContentCacheValue:
    canonical_url: str
    text: str
    is_md: bool
    expire_at: datetime
    raw_html: str | None = None
    cache_variant: str = ""

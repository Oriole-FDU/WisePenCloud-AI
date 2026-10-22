from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RawFetchOutput:
    """fetcher 的原始响应；HTML 和 PDF 在内容处理阶段再转成正文。"""

    source_url: str
    headers: dict[str, str] = field(default_factory=dict)
    raw_html: str | None = None
    pdf_bytes: bytes | None = None


class WebFetcher(Protocol):
    async def fetch(self, url: str) -> RawFetchOutput:
        ...



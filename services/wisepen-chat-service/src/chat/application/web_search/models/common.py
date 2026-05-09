from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

__all__ = [
    "ImageResult",
    "SearchResult",
    "SearchResponse",
    "is_valid_result",
    "to_optional_str",
]


@dataclass(frozen=True, slots=True)
class ImageResult:
    """通用图片搜索结果"""

    url: str
    desc: Optional[str] = None
    source_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    resolution: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", self.url.strip())
        object.__setattr__(self, "desc", normalize_optional_str(self.desc))
        object.__setattr__(self, "source_url", normalize_optional_str(self.source_url))
        object.__setattr__(self, "thumbnail_url", normalize_optional_str(self.thumbnail_url))
        object.__setattr__(self, "resolution", normalize_optional_str(self.resolution))


@dataclass(frozen=True, slots=True)
class SearchResult:
    """通用网页搜索结果"""

    title: str
    url: str
    snippet: str
    images: Tuple[ImageResult, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "url", self.url.strip())
        object.__setattr__(self, "snippet", self.snippet.strip())
        object.__setattr__(self, "images", tuple(self.images))


def is_valid_result(result: SearchResult) -> bool:
    """只过滤明显无内容的结果，不判断与 query 的语义相关性。"""

    title = result.title.strip()
    url = result.url.strip()
    snippet = result.snippet.strip()

    if not url:
        return False

    if not title and not snippet:
        return False

    if len(title) + len(snippet) < 12:
        return False

    return True


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """通用搜索响应"""

    query: str
    results: Tuple[SearchResult, ...] = field(default_factory=tuple)
    answer: Optional[str] = None
    images: Tuple[ImageResult, ...] = field(default_factory=tuple)

    # fresh_cache / searxng / duckduckgo / stale_cache / tavily
    source: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "query", self.query.strip())
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(self, "answer", normalize_optional_str(self.answer))
        object.__setattr__(self, "images", tuple(self.images))
        object.__setattr__(self, "source", normalize_optional_str(self.source))


def normalize_optional_str(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    value = value.strip()
    return value or None


def to_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None

    return str(value)
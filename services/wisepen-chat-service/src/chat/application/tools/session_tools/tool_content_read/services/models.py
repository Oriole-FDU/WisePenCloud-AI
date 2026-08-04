from __future__ import annotations

from dataclasses import dataclass, field

from chat.application.utils.chunkers import LocatorKind, SourceSpan


@dataclass(frozen=True, slots=True)
class ToolContentSnapshotLocator:
    """缓存正文中的命名定位入口。"""

    locator_index: int
    name: str
    kind: LocatorKind
    start_offset: int
    end_offset: int


@dataclass(frozen=True, slots=True)
class ToolContentRankedReadRequest:
    content_ids: tuple[str, ...]
    query: str
    top_k: int = 10


@dataclass(frozen=True, slots=True)
class ToolContentRegexReadRequest:
    content_ids: tuple[str, ...]
    pattern: str
    max_matches: int = 10
    context_chars: int = 1000


@dataclass(frozen=True, slots=True)
class ToolContentWindow:
    """从权威原文读取出的上下文窗口。"""

    text: str
    start_offset: int
    end_offset: int
    source_spans: tuple[SourceSpan, ...] = ()
    locator_names: tuple[str, ...] = ()
    page_labels: tuple[str, ...] = ()
    section_paths: tuple[tuple[str, ...], ...] = ()
    anchor_labels: tuple[str, ...] = ()
    truncated: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolContentReadFailure:
    content_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ToolContentRegexMatch:
    content_id: str
    match_start: int
    match_end: int
    window: ToolContentWindow


@dataclass(frozen=True, slots=True)
class ToolContentRegexReadResult:
    matches: tuple[ToolContentRegexMatch, ...] = ()
    failed: tuple[ToolContentReadFailure, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolContentSnapshotResult:
    content_id: str
    content_type: str | None = None
    total_length: int | None = None
    locators: tuple[ToolContentSnapshotLocator, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ToolContentRankedReadItem:
    content_id: str
    rank: int
    score: float
    chunk_index: int
    window: ToolContentWindow


@dataclass(frozen=True, slots=True)
class ToolContentRankedReadResult:
    ranked: tuple[ToolContentRankedReadItem, ...] = ()
    failed: tuple[ToolContentReadFailure, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolContentReadResult:
    content_id: str
    window: ToolContentWindow | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ToolContentLocatorReadResult:
    content_id: str
    locator: str
    windows: tuple[ToolContentWindow, ...] = ()
    reason: str | None = None

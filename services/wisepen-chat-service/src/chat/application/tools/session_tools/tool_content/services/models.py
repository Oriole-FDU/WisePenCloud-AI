from __future__ import annotations

from dataclasses import dataclass, field

from chat.application.utils.chunkers import SourceSpan


@dataclass(frozen=True, slots=True)
class ToolContentSnapshotPage:
    """缓存正文中的页入口。"""

    page_label: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True, slots=True)
class ToolContentSnapshotSection:
    """缓存正文中的 Section 结构节点。"""

    title: str
    section_path: str
    start_offset: int
    end_offset: int
    has_content: bool
    children: tuple["ToolContentSnapshotSection", ...] = ()


@dataclass(frozen=True, slots=True)
class ToolContentSnapshotAnchor:
    """缓存正文中的锚点入口。"""

    anchor_label: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True, slots=True)
class ToolContentSemanticSearchRequest:
    content_ids: tuple[str, ...]
    query: str
    top_k: int = 10


@dataclass(frozen=True, slots=True)
class ToolContentRegexSearchRequest:
    content_ids: tuple[str, ...]
    pattern: str
    max_matches: int = 10
    context_chars: int | None = None


@dataclass(frozen=True, slots=True)
class ToolContentWindow:
    """从权威原文读取出的上下文窗口。"""

    text: str
    start_offset: int
    end_offset: int
    source_spans: tuple[SourceSpan, ...] = ()
    page_labels: tuple[str, ...] = ()
    section_paths: tuple[str, ...] = ()
    anchor_labels: tuple[str, ...] = ()
    truncated: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolContentReadFailure:
    content_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ToolContentRegexSearchMatch:
    content_id: str
    match_start: int
    match_end: int
    window: ToolContentWindow


@dataclass(frozen=True, slots=True)
class ToolContentRegexSearchResult:
    matches: tuple[ToolContentRegexSearchMatch, ...] = ()
    failed: tuple[ToolContentReadFailure, ...] = ()
    budget_exhausted: bool = False


@dataclass(frozen=True, slots=True)
class ToolContentSnapshotResult:
    content_id: str
    content_type: str | None = None
    total_length: int | None = None
    pages: tuple[ToolContentSnapshotPage, ...] = ()
    sections: tuple[ToolContentSnapshotSection, ...] = ()
    anchors: tuple[ToolContentSnapshotAnchor, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ToolContentSemanticSearchItem:
    content_id: str
    rank: int
    score: float
    chunk_index: int
    window: ToolContentWindow


@dataclass(frozen=True, slots=True)
class ToolContentSemanticSearchResult:
    results: tuple[ToolContentSemanticSearchItem, ...] = ()
    failed: tuple[ToolContentReadFailure, ...] = ()
    budget_exhausted: bool = False


@dataclass(frozen=True, slots=True)
class ToolContentRangeReadResult:
    content_id: str
    window: ToolContentWindow | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ToolContentPageReadItem:
    page_label: str
    windows: tuple[ToolContentWindow, ...] = ()
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ToolContentPageReadResult:
    content_id: str
    items: tuple[ToolContentPageReadItem, ...] = ()
    budget_exhausted: bool = False


@dataclass(frozen=True, slots=True)
class ToolContentSectionReadItem:
    section_path: str
    windows: tuple[ToolContentWindow, ...] = ()
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ToolContentSectionReadResult:
    content_id: str
    items: tuple[ToolContentSectionReadItem, ...] = ()
    budget_exhausted: bool = False

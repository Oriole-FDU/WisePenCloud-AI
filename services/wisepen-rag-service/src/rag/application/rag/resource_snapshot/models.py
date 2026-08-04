from __future__ import annotations

from dataclasses import dataclass, field

from rag.utils.chunkers import LocatorKind, SourceSpan


@dataclass(frozen=True, slots=True)
class RagContentLocator:
    """资源副本中的命名定位。"""

    locator_index: int
    name: str
    kind: LocatorKind
    start_offset: int
    end_offset: int


@dataclass(frozen=True, slots=True)
class RagResourceSnapshot:
    """资源的解析后文档结构。"""

    resource_id: str
    document_version: int
    content_revision: str
    total_length: int
    pages: tuple["RagResourceSnapshotPage", ...] = ()
    sections: tuple["RagResourceSnapshotSection", ...] = ()


@dataclass(frozen=True, slots=True)
class RagResourceSnapshotPage:
    """可按页读取的结构入口。"""

    page_label: str


@dataclass(frozen=True, slots=True)
class RagResourceSnapshotSection:
    """可按 Section 读取的结构树节点。"""

    section_id: str
    title: str
    level: int
    section_path: tuple[str, ...]
    has_content: bool
    children: tuple["RagResourceSnapshotSection", ...] = ()


@dataclass(frozen=True, slots=True)
class RagResourceContentWindow:
    """从资源副本读取出的窗口。"""

    text: str
    start_offset: int
    end_offset: int
    source_spans: tuple[SourceSpan, ...] = ()
    page_labels: tuple[str, ...] = ()
    section_paths: tuple[tuple[str, ...], ...] = ()
    anchor_labels: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RagResourceContentReadResult:
    """资源副本读取结果。"""

    resource_id: str
    content_revision: str | None = None
    document_version: int | None = None
    items: tuple["RagResourceContentItem", ...] = ()


@dataclass(frozen=True, slots=True)
class RagResourceContentItem:
    """一次批量读取中的单个 page/section 结果。"""

    key: str
    kind: str
    reason: str | None = None
    windows: tuple[RagResourceContentWindow, ...] = ()

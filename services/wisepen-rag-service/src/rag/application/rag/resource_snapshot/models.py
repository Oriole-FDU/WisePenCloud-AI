from __future__ import annotations

from dataclasses import dataclass, field

from common.utils.chunkers import LocatorKind, SourceSpan


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
    """资源的解析后副本索引。"""

    resource_id: str
    document_version: int
    content_revision: str
    total_length: int
    locators: tuple[RagContentLocator, ...]


@dataclass(frozen=True, slots=True)
class RagResourceContentWindow:
    """从资源副本读取出的窗口。"""

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
class RagResourceContentReadResult:
    """资源副本读取结果。"""

    resource_id: str
    content_revision: str | None = None
    document_version: int | None = None
    locator_name: str | None = None
    reason: str | None = None
    windows: tuple[RagResourceContentWindow, ...] = ()

from __future__ import annotations

from dataclasses import dataclass, field

from chat.application.utils.chunkers import LocatorKind, SourceSpan, TextLocator


@dataclass(frozen=True, slots=True)
class ToolContentChunk:
    """用于语义检索的 chunk 及其权威原文范围。"""

    chunk_index: int
    source_spans: tuple[SourceSpan, ...]
    section_paths: tuple[tuple[str, ...], ...] = ()
    page_labels: tuple[str, ...] = ()
    anchor_labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StoredToolContent:
    """持久化的工具内容实体。"""

    content_id: str
    session_id: str
    content_type: str
    text: str
    chunks: tuple[ToolContentChunk, ...] = ()
    locators: tuple[TextLocator, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolContentReceipt:
    """工具内容入库后返回给调用方的存储凭证。"""

    content_id: str
    chunk_count: int
    locator_count: int
    locator_kinds: tuple[LocatorKind, ...]
    total_length: int
    metadata: dict[str, object] = field(default_factory=dict)

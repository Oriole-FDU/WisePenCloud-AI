from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ToolContentChunk:
    """ToolContent 中持久化的 chunk 元数据。"""

    chunk_index: int
    start_offset: int | None = None
    end_offset: int | None = None
    block_kinds: tuple[str, ...] = ()
    section_path: tuple[str, ...] = ()
    page_label: str | None = None
    anchor_labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolContentIndexEntry:
    """ToolContent 读取索引项。"""

    locator_name: str
    locator_kind: str
    chunk_indices: tuple[int, ...]
    start_offset: int | None = None
    end_offset: int | None = None
    section_path: tuple[str, ...] = ()
    page_label: str | None = None
    anchor_label: str | None = None


@dataclass(frozen=True, slots=True)
class ToolContentIndex:
    """ToolContent 的读取索引集合。"""

    entries: tuple[ToolContentIndexEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class StoredToolContent:
    """持久化的工具内容实体。"""

    content_id: str
    session_id: str
    content_type: str
    text: str
    chunks: tuple[ToolContentChunk, ...] = ()
    index: ToolContentIndex | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolContentReceipt:
    """工具内容入库后返回给调用方的存储凭证。"""

    content_id: str
    chunk_count: int
    supported_selectors: tuple[str, ...] = ()

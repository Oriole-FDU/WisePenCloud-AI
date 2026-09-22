from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class BlockKind(StrEnum):
    """文档解析阶段识别出的块级结构。"""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    FIGURE = "figure"
    CODE = "code"
    FORMULA = "formula"
    LIST = "list"
    QUOTE = "quote"
    PAGE_MARKER = "page_marker"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """原文中的 Python 字符半开区间。"""

    start_offset: int
    end_offset: int

    def __post_init__(self) -> None:
        # 所有下游都依赖 Python 字符半开区间；在 Common 边界一次性拒绝非法坐标。
        if self.start_offset < 0 or self.end_offset < self.start_offset:
            raise ValueError(
                "source span must satisfy 0 <= start_offset <= end_offset"
            )

    @property
    def length(self) -> int:
        """返回半开区间覆盖的 Python 字符数。"""
        return self.end_offset - self.start_offset


@dataclass(frozen=True, slots=True)
class DocumentBlock:
    """parser 产生或 oversized block 递归切分得到的结构单元。"""

    block_id: str
    text: str
    block_kind: BlockKind
    block_index: int
    # 均为原文 Python 字符半开区间；oversized 子块会把局部坐标平移回全文。
    start_offset: int
    end_offset: int
    section_path: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    """用于语义召回且能通过 source_spans 精确回源的文本分块。"""

    chunk_id: str
    text: str
    chunk_index: int
    # chunk 的包围范围，source_spans 保留其中各完整 block/子块的精确范围。
    start_offset: int
    end_offset: int
    source_spans: tuple[SourceSpan, ...]
    start_block: int
    end_block: int
    # 有真实 Section 时，一个 chunk 只归属一个 Section；flat 文本保持为空。
    section_id: str | None = None
    page_labels: tuple[str, ...] = ()
    anchor_labels: tuple[str, ...] = ()
    content_hash: str = ""


@dataclass(frozen=True, slots=True)
class Page:
    """一个页标签在原文中的确定范围。"""

    page_index: int
    page_label: str
    source_span: SourceSpan


@dataclass(frozen=True, slots=True)
class Anchor:
    """表格、图片或公式锚点及其精确原文范围。"""

    label: str
    source_span: SourceSpan


@dataclass(frozen=True, slots=True)
class Section:
    """标题树中的 Section 及其直属正文和子树范围。"""

    section_id: str
    title: str
    level: int
    parent_section_id: str | None
    ordinal: int
    section_path: tuple[str, ...]
    own_span: SourceSpan
    subtree_span: SourceSpan
    # 直属正文不包含标题、页标和子 Section，按这些 span 确定性读取。
    content_spans: tuple[SourceSpan, ...] = ()
    preview: str = ""


@dataclass(slots=True)
class OutlineNode:
    """模型可见的精简目录节点，不暴露内部 offset 和 section_path。"""

    section_id: str
    title: str
    # 节点可见章节范围的 Python 字符长度；真实章节包含其子章节。
    length: int
    page_range: str | None = None
    anchor_labels: list[str] = field(default_factory=list)
    children: list[OutlineNode] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DocumentChunkingResult:
    """一次文档解析和分块产生的完整结构事实。"""

    chunks: tuple[DocumentChunk, ...]
    blocks: tuple[DocumentBlock, ...]
    sections: tuple[Section, ...]
    pages: tuple[Page, ...]
    anchors: tuple[Anchor, ...]

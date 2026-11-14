from __future__ import annotations
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping


class MarkdownNodeKind(StrEnum):
    """Markdown 节点类型"""

    SECTION = "section"
    PARAGRAPH = "paragraph"
    LIST = "list"
    LIST_ITEM = "list_item"
    QUOTE = "quote"
    TABLE = "table"
    TABLE_HEADER = "table_header"
    TABLE_ROW = "table_row"
    TABLE_CELL = "table_cell"
    CODE = "code"
    FORMULA = "formula"
    FIGURE = "figure"
    INLINE = "inline"


@dataclass(frozen=True, slots=True)
class Span:
    """Text的区间块"""

    start_offset: int
    end_offset: int

    def __post_init__(self) -> None:
        if self.start_offset < 0 or self.end_offset < self.start_offset:
            raise ValueError("span must satisfy 0 <= start_offset <= end_offset")

    @property
    def length(self) -> int:
        return self.end_offset - self.start_offset


@dataclass(frozen=True, slots=True)
class MarkdownNode:
    """Markdown 节点"""
    node_id: str
    kind: MarkdownNodeKind
    text: str
    source_spans: list[Span] = field(default_factory=list)
    children: list[MarkdownNode] = field(default_factory=list)

    # 常用 metadata 键：
    # heading_level         SECTION 的标题层级（# 为 1）
    # title / section_path  归一化后的标题与章节路径
    # ordered / start       LIST 的有序标志与起始序号
    # language / fenced     CODE 的语言与围栏标志
    # anchor_label          TABLE / FIGURE / FORMULA 的编号题注标签
    # caption / figure_text TABLE / FIGURE 的题注与挂接后文本
    # source_node_ids       拆分后 part 节点的原始归属
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def source_node_ids(self) -> list[str]:
        """结构来源归属，不保证与派生片段的文本范围精确对应。"""

        identities = self.metadata.get("source_node_ids")
        if identities is not None:
            return list(identities)
        return [
            self.node_id,
            *(identity for child in self.children for identity in child.source_node_ids),
        ]

    @property
    def start(self) -> int | None:
        return min((span.start_offset for span in self.source_spans), default=None)

    @property
    def end(self) -> int | None:
        return max((span.end_offset for span in self.source_spans), default=None)

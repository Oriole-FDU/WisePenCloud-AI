"""RAG 索引流程对 Common 文档事实的轻量聚合。"""

from dataclasses import dataclass, field
from enum import StrEnum

from common.utils.document import Anchor, Page, Section


class StructureMode(StrEnum):
    """权威正文可提供的结构层级。"""

    SECTIONED = "sectioned"
    FLAT_TEXT = "flat_text"
    EMPTY = "empty"


@dataclass(slots=True)
class DocumentStructure:
    """一次文档解析产生的 RAG 模式和 Common 结构事实。"""

    mode: StructureMode
    total_length: int
    sections: list[Section] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    anchors: list[Anchor] = field(default_factory=list)

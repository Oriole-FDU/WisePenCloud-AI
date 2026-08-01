from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

Metadata = dict[str, object]


class BlockKind(StrEnum):
    """Markdown 解析阶段识别出的结构块类型。"""

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


class ChunkerKind(StrEnum):
    """模块公开支持的固定分块设施。"""

    PLAIN_TEXT = "plain_text"
    MARKDOWN = "markdown"


class LocatorKind(StrEnum):
    """Markdown 原文支持的命名定位维度。"""

    SECTION = "section"
    PAGE = "page"
    ANCHOR = "anchor"


@dataclass(frozen=True, slots=True)
class ChunkDocument:
    """分块输入；offset 始终相对于 `text` 计算。"""

    text: str  # 原始正文
    document_id: str | None = None  # 可选文档标识
    content_type: str | None = None  # 可选 MIME 类型
    title: str | None = None  # 可选文档标题
    metadata: Metadata = field(default_factory=dict)  # 调用方附带的文档元信息


@dataclass(frozen=True, slots=True)
class TextBlock:
    """解析或递归切分产生的中间结构单元。"""

    block_id: str  # 当前文档内的块标识
    text: str  # 保留原格式的块文本
    block_kind: BlockKind = BlockKind.PARAGRAPH  # 结构语义
    block_index: int = 0  # 当前文档内的顺序索引
    start_offset: int | None = None  # 原文起始位置，左闭
    end_offset: int | None = None  # 原文结束位置，右开
    section_path: tuple[str, ...] = ()  # 从一级标题到当前标题的完整路径
    metadata: Metadata = field(default_factory=dict)  # 解析器补充的结构信息


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """最终 chunk 引用的原文半开区间。"""

    start_offset: int
    end_offset: int


@dataclass(frozen=True, slots=True)
class Chunk:
    """可持久化或索引的最终分块。"""

    chunk_id: str  # 归一化前为临时 ID，归一化后为稳定 ID
    text: str  # 分块正文
    chunk_index: int  # 最终结果中的连续顺序索引
    start_offset: int | None = None  # 原文起始位置，左闭
    end_offset: int | None = None  # 原文结束位置，右开
    source_spans: tuple[SourceSpan, ...] = ()  # 实际参与该 chunk 的原文范围
    start_block: int | None = None  # 覆盖的首个结构块索引
    end_block: int | None = None  # 覆盖的末个结构块索引
    content_hash: str = ""  # 最终文本的 SHA-256
    metadata: Metadata = field(default_factory=dict)  # 页码、块类型等结构信息


@dataclass(frozen=True, slots=True)
class TextLocator:
    """章节、页码或锚点在原文中的确定性定位范围。"""

    name: str  # 带类型前缀的稳定名称
    kind: LocatorKind  # 定位维度
    start_offset: int  # 定位范围起点，左闭
    end_offset: int  # 定位范围终点，右开


@dataclass(frozen=True, slots=True)
class ChunkingResult:
    """一次分块的完整输出，包括中间块和语义定位。"""

    chunks: tuple[Chunk, ...]  # 最终分块
    chunker: ChunkerKind  # 实际使用的设施
    blocks: tuple[TextBlock, ...] = ()  # 解析产生的中间块
    locators: tuple[TextLocator, ...] = ()  # Markdown 原文定位项
    metadata: Metadata = field(default_factory=dict)  # 本次处理的统计信息

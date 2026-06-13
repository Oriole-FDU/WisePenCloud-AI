from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

type Metadata = dict[str, object]


class UnitType(StrEnum):
    """TextUnit 的语义块类型。"""

    HEADING = "heading"          # 标题（# ~ ######）
    PARAGRAPH = "paragraph"      # 普通段落
    TABLE = "table"              # Markdown 表格
    CODE = "code"                # 围栏代码块
    FORMULA = "formula"          # 数学公式（$$ 或 \[...\]）
    IMAGE = "image"              # 图片（![alt](url)）
    LIST = "list"                # 有序/无序列表
    QUOTE = "quote"              # 引用块（>）
    PAGE_MARKER = "page_marker"  # 页码标记，统一格式：<!-- page N -->
    UNKNOWN = "unknown"          # 未识别类型


class ChunkLevel(StrEnum):
    """Chunk 的用途层级。"""

    READ = "read"        # 阅读级：大 chunk
    RETRIEVE = "retrieve"  # 检索级：中 chunk
    SEARCH = "search"    # 搜索级：小 chunk
    DEFAULT = "default"  # 默认


class IndexKind(StrEnum):
    """ChunkIndex 的索引类型，提供按不同维度查找 chunk 的能力。

    Chunk 本身已有 chunk_index（顺序）和 start_offset/end_offset（位置），
    构成天然的连续索引。IndexKind 提供的是额外的语义维度索引。
    """

    SECTION = "section"  # 按章节名定位 chunk
    PAGE = "page"        # 按页码定位 chunk
    ANCHOR = "anchor"    # 按锚标（Table/Figure/Equation）定位 chunk


@dataclass(frozen=True, slots=True)
class ChunkDocument:
    """待分块的原始文档。

    分块流程的输入，包含原始文本和可选的文档元信息。
    """

    text: str  # 原始文本
    document_id: str | None = None  # 可选文档 ID
    content_type: str | None = None  # 内容类型，如 text/markdown、text/plain
    title: str | None = None  # 可选标题
    metadata: Metadata = field(default_factory=dict)  # 额外元信息


@dataclass(frozen=True, slots=True)
class TextUnit:
    """切分过程中的结构/语义单元。

    splitter 的输出，代表文档中一个不可再分的语义块，
    如一个标题、一个段落、一个代码块、一个表格等。
    packer 会把多个 TextUnit 聚合成最终的 Chunk。
    """

    unit_id: str  # unit 唯一标识
    text: str  # unit 文本内容
    unit_type: UnitType = UnitType.PARAGRAPH  # 语义块类型
    unit_index: int = 0  # unit 在文档中的顺序（从 0 开始）
    start_offset: int | None = None  # 在原文中的起始字符偏移量
    end_offset: int | None = None  # 在原文中的结束字符偏移量
    section_path: tuple[str, ...] = ()  # 所属标题路径，如 ("快速开始", "安装")
    metadata: Metadata = field(default_factory=dict)  # 额外元信息


@dataclass(frozen=True, slots=True)
class Chunk:
    """最终输出的分块。

    分块流程的核心输出，由 packer 将多个 TextUnit 聚合而成，
    或由 engine 在无 packer 时从 TextUnit 一对一映射而来。

    嵌套分块时，父 chunk（level=READ）用于上下文注入，
    子 chunk（level=RETRIEVE）用于精准检索，通过 parent_chunk_id 关联。
    """

    chunk_id: str  # chunk 唯一标识
    text: str  # chunk 文本内容
    chunk_index: int  # chunk 在同 level 中的顺序（从 0 开始，由 engine 自动分配）
    level: ChunkLevel = ChunkLevel.DEFAULT  # 用途层级
    parent_chunk_id: str | None = None  # 父 chunk ID（嵌套分块时，子 chunk 指向父 chunk）
    start_offset: int | None = None  # 在原文中的起始字符偏移量
    end_offset: int | None = None  # 在原文中的结束字符偏移量
    start_unit: int | None = None  # 起始 unit index
    end_unit: int | None = None  # 结束 unit index
    content_hash: str = ""  # 内容 SHA-256 哈希（由 ChunkFinalizer 填充）
    metadata: Metadata = field(default_factory=dict)  # 额外元信息


@dataclass(frozen=True, slots=True)
class ChunkIndex:
    """chunk 额外语义索引，提供按不同维度查找 chunk 的能力。

    Chunk 本身已有 chunk_index（顺序）和 start_offset/end_offset（位置），
    构成天然的连续索引。ChunkIndex 提供额外的语义维度索引，
    如按章节名、页码、锚标等定位 chunk。
    """

    name: str  # 索引名称，如 "section:快速开始 > 安装"、"page:3"
    kind: IndexKind  # 索引类型
    chunk_indices: tuple[int, ...]  # 命中的 chunk index 列表
    chunk_ids: tuple[str, ...] = ()  # 命中的 chunk ID 列表
    start_offset: int | None = None  # 索引覆盖的起始 offset
    end_offset: int | None = None  # 索引覆盖的结束 offset
    metadata: Metadata = field(default_factory=dict)  # 额外元信息


@dataclass(frozen=True, slots=True)
class ChunkingResult:
    """分块流程的最终结果。"""

    chunks: tuple[Chunk, ...]  # 输出的 chunk 列表
    units: tuple[TextUnit, ...] = ()  # 中间产生的 unit 列表（供索引器使用）
    indexes: tuple[ChunkIndex, ...] = ()  # 定位索引列表
    pipeline: str = ""  # 使用的 pipeline 名称
    metadata: Metadata = field(default_factory=dict)  # 额外元信息（含 unit_count / chunk_count / index_count）

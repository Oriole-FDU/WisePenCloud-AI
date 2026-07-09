from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

Metadata = dict[str, object]


class BlockKind(StrEnum):
    """TextBlock 的语义块类型。"""

    HEADING = "heading"  # 标题（# ~ ######）
    PARAGRAPH = "paragraph"  # 普通段落
    TABLE = "table"  # Markdown 表格
    CODE = "code"  # 围栏代码块
    FORMULA = "formula"  # 数学公式（$$ 或 \[...\]）
    IMAGE = "image"  # 图片（![alt](url)）
    LIST = "list"  # 有序/无序列表
    QUOTE = "quote"  # 引用块（>）
    PAGE_MARKER = "page_marker"  # 页码标记，统一格式：<!-- page N -->
    UNKNOWN = "unknown"  # 未识别类型


class ChunkRole(StrEnum):
    """Chunk 在分块结果中的结构角色。"""

    FLAT = "flat"  # 单层分块产出的普通 chunk
    PARENT = "parent"  # 父子分块中的父 chunk，用于上下文注入
    CHILD = "child"  # 父子分块中的子 chunk，用于精准检索


class LocatorKind(StrEnum):
    """ChunkLocator 的定位类型，提供按不同维度查找 chunk 的能力。

    Chunk 本身已有 chunk_index（顺序）和 start_offset/end_offset（位置），
    构成天然的连续定位。LocatorKind 提供的是语义定位维度。
    """

    SECTION = "section"  # 按章节名定位 chunk
    PAGE = "page"  # 按页码定位 chunk
    ANCHOR = "anchor"  # 按锚标（Table/Figure/Equation）定位 chunk


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
class TextBlock:
    """切分过程中的结构/语义单元。

    block_splitter 的输出，代表文档中一个不可再分的语义块，
    如一个标题、一个段落、一个代码块、一个表格等。
    block_packer 会把多个 TextBlock 聚合成最终的 Chunk。
    """

    block_id: str  # block 唯一标识
    text: str  # block 文本内容
    block_kind: BlockKind = BlockKind.PARAGRAPH  # 语义块类型
    block_index: int = 0  # block 在文档中的顺序（从 0 开始）
    start_offset: int | None = None  # 在原文中的起始字符偏移量
    end_offset: int | None = None  # 在原文中的结束字符偏移量
    section_path: tuple[str, ...] = ()  # 所属标题路径，如 ("快速开始", "安装")
    metadata: Metadata = field(default_factory=dict)  # 额外元信息


@dataclass(frozen=True, slots=True)
class Chunk:
    """最终输出的分块。

    分块流程的核心输出，由 block_packer 将多个 TextBlock 聚合而成，
    或由 engine 在无 block_packer 时从 TextBlock 一对一映射而来。

    父子分块时，父 chunk（role=PARENT）用于上下文注入，
    子 chunk（role=CHILD）用于精准检索，通过 parent_chunk_id 关联。
    """

    chunk_id: str  # chunk 唯一标识
    text: str  # chunk 文本内容
    chunk_index: int  # chunk 在结果中的顺序（从 0 开始，由 engine 自动分配）
    role: ChunkRole = ChunkRole.FLAT  # chunk 在结果中的结构角色
    parent_chunk_id: str | None = None  # 父 chunk ID（嵌套分块时，子 chunk 指向父 chunk）
    start_offset: int | None = None  # 在原文中的起始字符偏移量
    end_offset: int | None = None  # 在原文中的结束字符偏移量
    start_block: int | None = None  # 起始 block index
    end_block: int | None = None  # 结束 block index
    content_hash: str = ""  # 内容 SHA-256 哈希（由 normalizer 填充）
    metadata: Metadata = field(default_factory=dict)  # 额外元信息


@dataclass(frozen=True, slots=True)
class ChunkLocator:
    """chunk 语义定位项，提供按不同维度查找 chunk 的能力。

    Chunk 本身已有 chunk_index（顺序）和 start_offset/end_offset（位置），
    构成天然的连续定位。ChunkLocator 提供语义定位项，
    如按章节名、页码、锚标等定位 chunk。
    """

    name: str  # 定位名称，如 "section:快速开始 > 安装"、"page:3"
    kind: LocatorKind  # 定位类型
    chunk_indices: tuple[int, ...]  # 命中的 chunk index 列表
    chunk_ids: tuple[str, ...] = ()  # 命中的 chunk ID 列表
    start_offset: int | None = None  # 索引覆盖的起始 offset
    end_offset: int | None = None  # 索引覆盖的结束 offset
    metadata: Metadata = field(default_factory=dict)  # 额外元信息


@dataclass(frozen=True, slots=True)
class ChunkingResult:
    """分块流程的最终结果。"""

    chunks: tuple[Chunk, ...]  # 输出的 chunk 列表
    blocks: tuple[TextBlock, ...] = ()  # 中间产生的 block 列表（供定位器使用）
    locators: tuple[ChunkLocator, ...] = ()  # 定位项列表
    pipeline: str = ""  # 使用的 pipeline 名称
    metadata: Metadata = field(default_factory=dict)  # 额外元信息（含 block_count / chunk_count / locator_count）

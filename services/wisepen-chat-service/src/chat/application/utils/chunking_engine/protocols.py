from __future__ import annotations

from typing import Protocol

from .models import Chunk, ChunkDocument, ChunkLocator, TextBlock


class BlockSplitter(Protocol):
    """block 切分器协议，把文档拆成结构/语义块（TextBlock）。

    这是分块流程的核心步骤，不同 block_splitter 产生不同粒度的 block：
    - MarkdownBlockSplitter：按 Markdown 结构（标题、段落、代码块、表格等）切分
    - RecursiveTextBlockSplitter：按递归字符分隔符切分（适用于无结构文本）
    """

    name: str  # 切分器名称

    def split(self, *, document: ChunkDocument) -> tuple[TextBlock, ...]:
        """切分文档为 block 列表。"""
        ...


class BlockPacker(Protocol):
    """chunk 聚合器协议，把多个 TextBlock 聚合成 Chunk。

    block_packer 负责控制最终 chunk 的大小：将相邻的小 block 合并、
    将过大的 block 组合控制在目标 chunk_size 内。
    不会从 block 中间切开，保证 block 完整性。

    当 pipeline 不配置 block_packer 时，engine 会自动将每个 block 一对一映射为 chunk。
    """

    name: str  # 聚合器名称

    def pack(self, *, blocks: tuple[TextBlock, ...]) -> tuple[Chunk, ...]:
        """把 block 列表聚合为 chunk 列表。"""
        ...


class ChunkDeriver(Protocol):
    """chunk 派生器协议，从已有 chunk 生成新的关联 chunk。

    典型用途：基于父 chunk 继续拆分出 child chunk，用于精准检索。
    """

    name: str  # 派生器名称

    def process(self, *, chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
        """处理 chunk 列表，返回原 chunk 与派生 chunk。"""
        ...


class ChunkNormalizer(Protocol):
    """chunk 规范化器协议，对 chunk 做最终结构修正。

    典型用途：合并纯标题 chunk、合并短尾 chunk、生成稳定 ID、维护父子引用等。
    """

    name: str  # 规范化器名称

    def process(self, *, chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
        """处理 chunk 列表，返回规范化后的 chunk 列表。"""
        ...


class ChunkLocatorBuilder(Protocol):
    """chunk 定位器协议，构建 chunk 的语义定位项（ChunkLocator）。

    Chunk 本身已有 chunk_index（顺序）和 start_offset/end_offset（位置），
    构成天然的连续定位。此协议构建的是语义维度定位项（如章节、页码、锚标），
    供下游按 kind 指定维度查找 chunk。
    """

    name: str  # 定位器名称

    def index(
            self,
            *,
            document: ChunkDocument,
            blocks: tuple[TextBlock, ...],
            chunks: tuple[Chunk, ...],
    ) -> tuple[ChunkLocator, ...]:
        """基于最终 chunk 构建额外语义定位项。"""
        ...

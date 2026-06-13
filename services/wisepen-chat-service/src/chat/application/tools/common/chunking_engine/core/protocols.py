from __future__ import annotations

from typing import Protocol

from .models import Chunk, ChunkDocument, ChunkIndex, TextUnit


class PreProcessor(Protocol):
    """预处理器协议，在切分前对原始文档进行转换。

    典型用途：为 Markdown 标题下的正文注入标题路径前缀，
    使后续切分出的 chunk 自带上下文信息。
    """

    name: str  # 预处理器名称

    def process(self, *, document: ChunkDocument) -> ChunkDocument:
        """处理待分块文档，返回转换后的文档。"""
        ...


class UnitSplitter(Protocol):
    """unit 切分器协议，把文档拆成结构/语义单元（TextUnit）。

    这是分块流程的核心步骤，不同 splitter 产生不同粒度的 unit：
    - MarkdownBlockSplitter：按 Markdown 结构（标题、段落、代码块、表格等）切分
    - RecursiveTextSplitter：按递归字符分隔符切分（适用于无结构文本）
    """

    name: str  # 切分器名称

    def split(self, *, document: ChunkDocument) -> tuple[TextUnit, ...]:
        """切分文档为 unit 列表。"""
        ...


class ChunkPacker(Protocol):
    """chunk 聚合器协议，把多个 TextUnit 聚合成 Chunk。

    packer 负责控制最终 chunk 的大小：将相邻的小 unit 合并、
    将过大的 unit 组合控制在目标 chunk_size 内。
    不会从 unit 中间切开，保证 unit 完整性。

    当 pipeline 不配置 packer 时，engine 会自动将每个 unit 一对一映射为 chunk。
    """

    name: str  # 聚合器名称

    def pack(self, *, units: tuple[TextUnit, ...]) -> tuple[Chunk, ...]:
        """把 unit 列表聚合为 chunk 列表。"""
        ...


class ChunkPostProcessor(Protocol):
    """chunk 后处理器协议，对聚合后的 chunk 进行修正或增强。

    典型用途：合并纯标题 chunk、合并短尾 chunk、生成稳定 ID 等。
    """

    name: str  # 后处理器名称

    def process(self, *, chunks: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
        """处理 chunk 列表，返回修正后的 chunk 列表。"""
        ...


class ChunkExtraIndexer(Protocol):
    """chunk 额外语义索引器协议，构建 chunk 的额外定位索引（ChunkIndex）。

    Chunk 本身已有 chunk_index（顺序）和 start_offset/end_offset（位置），
    构成天然的连续索引。此协议构建的是额外的语义维度索引（如章节、页码、锚标），
    供下游按 kind 指定维度查找 chunk。
    """

    name: str  # 索引器名称

    def index(
        self,
        *,
        document: ChunkDocument,
        units: tuple[TextUnit, ...],
        chunks: tuple[Chunk, ...],
    ) -> tuple[ChunkIndex, ...]:
        """基于最终 chunk 构建额外语义索引。"""
        ...

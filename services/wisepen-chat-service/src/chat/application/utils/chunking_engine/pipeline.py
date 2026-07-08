from __future__ import annotations

from dataclasses import dataclass

from .protocols import (
    BlockPacker,
    BlockSplitter,
    ChunkDeriver,
    ChunkLocatorBuilder,
    ChunkNormalizer,
    DocumentEnricher,
)


@dataclass(frozen=True, slots=True)
class ChunkingPipeline:
    """分块流水线配置，定义分块的完整流程。

    执行顺序：document_enrichers → block_splitter → block_packer → chunk_derivers → chunk_normalizers → chunk_locator

    - document_enrichers：文档转换（如注入标题路径），在切分前修改原文
    - block_splitter：切分为 TextBlock，是流程的核心
    - block_packer：将 TextBlock 聚合为 Chunk（可选，None 时 block 一对一映射为 chunk）
    - chunk_derivers：从已有 chunk 派生新 chunk（如父块拆出子块）
    - chunk_normalizers：规范化 chunk（如合并短 chunk、生成稳定 ID、维护引用）
    - chunk_locator：构建语义定位索引（可选，供下游按维度查找 chunk）
    """

    name: str  # pipeline 名称，如 "markdown"、"plain_text"
    block_splitter: BlockSplitter  # block 切分器（必选）
    block_packer: BlockPacker | None = None  # chunk 聚合器，None 时 block 一对一映射为 chunk
    document_enrichers: tuple[DocumentEnricher, ...] = ()  # 文档转换器列表，按顺序执行
    chunk_derivers: tuple[ChunkDeriver, ...] = ()  # chunk 派生器列表，按顺序执行
    chunk_normalizers: tuple[ChunkNormalizer, ...] = ()  # chunk 规范化器列表，按顺序执行
    chunk_locator: ChunkLocatorBuilder | None = None  # 语义定位器（可选）

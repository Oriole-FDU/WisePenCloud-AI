from __future__ import annotations

from dataclasses import dataclass

from .protocols import (
    ChunkExtraIndexer,
    ChunkPacker,
    ChunkPostProcessor,
    PreProcessor,
    UnitSplitter,
)


@dataclass(frozen=True, slots=True)
class ChunkingPipeline:
    """分块流水线配置，定义分块的完整流程。

    执行顺序：pre_processors → splitter → packer → post_processors → extra_indexer

    - pre_processors：预处理（如注入标题路径），在切分前修改原文
    - splitter：切分为 TextUnit，是流程的核心
    - packer：将 TextUnit 聚合为 Chunk（可选，None 时 unit 一对一映射为 chunk）
    - post_processors：后处理（如合并短 chunk、生成 ID），修正聚合结果
    - extra_indexer：构建额外语义索引（可选，供下游按维度查找 chunk）
    """

    name: str  # pipeline 名称，如 "markdown"、"plain_text"
    splitter: UnitSplitter  # unit 切分器（必选）
    packer: ChunkPacker | None = None  # chunk 聚合器，None 时 unit 一对一映射为 chunk
    pre_processors: tuple[PreProcessor, ...] = ()  # 预处理器列表，按顺序执行
    post_processors: tuple[ChunkPostProcessor, ...] = ()  # 后处理器列表，按顺序执行
    extra_indexer: ChunkExtraIndexer | None = None  # 额外语义索引器（可选）

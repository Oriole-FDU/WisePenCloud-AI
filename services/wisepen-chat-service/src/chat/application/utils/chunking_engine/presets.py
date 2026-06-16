from __future__ import annotations

from .extra_indexers.chunk_extral_indexer import ChunkExtraIndexer
from .models import ChunkLevel
from .packers.block_aware_packer import BlockAwarePacker, BlockAwarePackerConfig
from .pipeline import ChunkingPipeline
from .post_processors.chunk_finalizer import ChunkFinalizer
from .post_processors.nested_chunk_processor import NestedChunkConfig, NestedChunkProcessor
from .pre_processors.markdown_pre_processor import MarkdownPreProcessor
from .splitters.markdown_block_splitter import MarkdownBlockSplitter
from .splitters.recursive_text_splitter import RecursiveTextSplitter, RecursiveTextSplitterConfig

DEFAULT_CHUNK_SIZE: int = 4000  # 默认 chunk 目标字符数


# 1. 标准 Markdown 分块 Pipeline
# 核心：标题路径注入 -> 结构块切分 -> 块聚合 -> 索引构建
# 适用：需要完整保留 Markdown 元素（标题/表格/代码块）结构的场景
MARKDOWN_PIPELINE = ChunkingPipeline(
    name="markdown",
    splitter=MarkdownBlockSplitter(),
    packer=BlockAwarePacker(
        BlockAwarePackerConfig(chunk_size=DEFAULT_CHUNK_SIZE, level=ChunkLevel.READ)
    ),
    pre_processors=(MarkdownPreProcessor(),),
    post_processors=(ChunkFinalizer(),),
    extra_indexer=ChunkExtraIndexer(),
)


# 2. 纯文本分块 Pipeline
# 核心：递归字符切分 -> 短尾合并（无 packer 聚合，无定位索引）
# 适用：content_type=text/plain 或无结构的普通文本文档
PLAIN_TEXT_PIPELINE = ChunkingPipeline(
    name="plain_text",
    splitter=RecursiveTextSplitter(
        RecursiveTextSplitterConfig(chunk_size=DEFAULT_CHUNK_SIZE, chunk_overlap=0)
    ),
    pre_processors=(),
    post_processors=(ChunkFinalizer(),),
)


# 3. Markdown 递归分块 Pipeline
# 核心：标题路径注入 -> 优先标题的递归切分 -> 索引构建（切分更均匀，不强制保留完整块）
# 适用：Markdown 文档且对 Chunk 大小均匀度要求较高的场景
MARKDOWN_RECURSIVE_PIPELINE = ChunkingPipeline(
    name="markdown_recursive",
    splitter=RecursiveTextSplitter(
        RecursiveTextSplitterConfig.for_markdown(chunk_size=DEFAULT_CHUNK_SIZE, chunk_overlap=0)
    ),
    pre_processors=(MarkdownPreProcessor(),),
    post_processors=(ChunkFinalizer(),),
    extra_indexer=ChunkExtraIndexer(),
)


# 4. 嵌套分块 Pipeline（Markdown）
# 核心：大 Chunk 聚合 -> 拆分小子 Chunk（子块通过 ID 关联父块）-> 索引构建
# 适用：需要“精准检索（子块）+ 完整上下文（父块）”的进阶 RAG 场景
NESTED_MARKDOWN_PIPELINE = ChunkingPipeline(
    name="nested_markdown",
    splitter=MarkdownBlockSplitter(),
    packer=BlockAwarePacker(
        BlockAwarePackerConfig(chunk_size=DEFAULT_CHUNK_SIZE, level=ChunkLevel.READ)
    ),
    pre_processors=(MarkdownPreProcessor(),),
    post_processors=(
        ChunkFinalizer(),
        NestedChunkProcessor(NestedChunkConfig(child_chunk_size=600, child_overlap=100)),
    ),
    extra_indexer=ChunkExtraIndexer(),
)
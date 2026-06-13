from __future__ import annotations

from .core.models import ChunkLevel
from .core.pipeline import ChunkingPipeline
from .extra_indexers.chunk_extral_indexer import ChunkExtraIndexer
from .packers.block_aware_packer import BlockAwarePacker, BlockAwarePackerConfig
from .post_processors.chunk_finalizer import ChunkFinalizer
from .post_processors.nested_chunk_processor import NestedChunkConfig, NestedChunkProcessor
from .pre_processors.markdown_pre_processor import MarkdownPreProcessor
from .splitters.markdown_block_splitter import MarkdownBlockSplitter
from .splitters.recursive_text_splitter import RecursiveTextSplitter, RecursiveTextSplitterConfig

# ---------------------------------------------------------------------------
# 模块级配置常量
# ---------------------------------------------------------------------------

DEFAULT_CHUNK_SIZE: int = 4000  # 默认 chunk 目标字符数

# ---------------------------------------------------------------------------
# Markdown 分块 pipeline
# 流程：标题路径注入 → 结构块切分 → 块感知聚合 → 终态处理 → 索引
#
# 1. MarkdownPreProcessor：为标题下的正文注入 "Section: 一级 > 二级" 前缀
# 2. MarkdownBlockSplitter：按 Markdown 结构切分为 TextUnit（标题/段落/代码块等）
# 3. BlockAwarePacker：将相邻 TextUnit 聚合成目标大小的 Chunk
# 4. ChunkFinalizer：合并纯标题 chunk、合并短尾 chunk、生成稳定 ID
# 5. ChunkExtraIndexer：构建 span/section/page/anchor 四种定位索引
#
# 适用：content_type=text/markdown 的文档
# ---------------------------------------------------------------------------
MARKDOWN_PIPELINE = ChunkingPipeline(
    name="markdown",
    splitter=MarkdownBlockSplitter(),
    packer=BlockAwarePacker(BlockAwarePackerConfig(chunk_size=DEFAULT_CHUNK_SIZE, level=ChunkLevel.READ)),
    pre_processors=(MarkdownPreProcessor(),),
    post_processors=(ChunkFinalizer(),),
    extra_indexer=ChunkExtraIndexer(),
)

# ---------------------------------------------------------------------------
# 纯文本分块 pipeline
# 流程：递归字符切分 → 终态处理
#
# 1. RecursiveTextSplitter：按递归分隔符直接切分为目标大小的 TextUnit
#    （已按目标大小切分，无需 packer 聚合，unit 一对一映射为 chunk）
# 2. ChunkFinalizer：合并短尾 chunk、生成稳定 ID
#
# 不配置 extra_indexer：RecursiveTextSplitter 只产出 PARAGRAPH unit，
# 没有结构化 unit（HEADING/TABLE/FORMULA/PAGE_MARKER），额外索引无意义。
#
# 适用：content_type=text/plain 或无结构文档
# ---------------------------------------------------------------------------
PLAIN_TEXT_PIPELINE = ChunkingPipeline(
    name="plain_text",
    splitter=RecursiveTextSplitter(RecursiveTextSplitterConfig(chunk_size=DEFAULT_CHUNK_SIZE, chunk_overlap=100)),
    pre_processors=(),
    post_processors=(ChunkFinalizer(),),
)

# ---------------------------------------------------------------------------
# Markdown 递归分块 pipeline
# 流程：标题路径注入 → 递归字符切分 → 终态处理 → 索引
#
# 1. MarkdownPreProcessor：为标题下的正文注入 "Section: 一级 > 二级" 前缀
# 2. RecursiveTextSplitter（for_markdown）：按 Markdown 标题优先的递归分隔符切分
#    （已按目标大小切分，无需 packer 聚合，unit 一对一映射为 chunk）
# 3. ChunkFinalizer：合并短尾 chunk、生成稳定 ID
# 4. ChunkExtraIndexer：构建 span/section/page/anchor 索引
#
# 适用：Markdown 文档但不需要保留完整结构块的场景，切分更均匀
# ---------------------------------------------------------------------------
MARKDOWN_RECURSIVE_PIPELINE = ChunkingPipeline(
    name="markdown_recursive",
    splitter=RecursiveTextSplitter(RecursiveTextSplitterConfig.for_markdown(chunk_size=DEFAULT_CHUNK_SIZE, chunk_overlap=100)),
    pre_processors=(MarkdownPreProcessor(),),
    post_processors=(ChunkFinalizer(),),
    extra_indexer=ChunkExtraIndexer(),
)

# ---------------------------------------------------------------------------
# 连续读取 pipeline
# 流程：递归字符切分（无重叠） → 终态处理 → 索引
#
# 1. RecursiveTextSplitter：按递归分隔符切分为目标大小的 TextUnit，chunk_overlap=0
#    （无重叠，chunk 之间严格连续，适合逐段读取场景）
# 2. ChunkFinalizer：合并短尾 chunk、生成稳定 ID
# 3. ChunkExtraIndexer：构建 span/page/anchor 索引（关闭 section）
#
# 适用：需要严格连续分段的读取场景，如电子书翻页、文档逐段浏览
# ---------------------------------------------------------------------------
SEQUENTIAL_READ_PIPELINE = ChunkingPipeline(
    name="sequential_read",
    splitter=RecursiveTextSplitter(RecursiveTextSplitterConfig(chunk_size=DEFAULT_CHUNK_SIZE, chunk_overlap=0)),
    pre_processors=(),
    post_processors=(ChunkFinalizer(),),
)

# ---------------------------------------------------------------------------
# 嵌套分块 pipeline（Markdown）
# 流程：标题路径注入 → 结构块切分 → 块感知聚合 → 终态处理 → 嵌套拆分 → 索引
#
# 1. MarkdownPreProcessor：为标题下的正文注入 "Section: 一级 > 二级" 前缀
# 2. MarkdownBlockSplitter：按 Markdown 结构切分为 TextUnit
# 3. BlockAwarePacker：将相邻 TextUnit 聚合成大 Chunk（父 chunk，level=READ）
# 4. ChunkFinalizer：合并纯标题 chunk、合并短尾 chunk、生成稳定 ID
# 5. NestedChunkProcessor：将父 chunk 拆分为子 chunk（level=RETRIEVE），
#    子 chunk 通过 parent_chunk_id 关联父 chunk
# 6. ChunkExtraIndexer：构建 span/section/page/anchor 四种定位索引
#
# 检索时命中子 chunk → 通过 parent_chunk_id 取回父 chunk 作为上下文
# 适用：需要精准检索 + 完整上下文的 RAG 场景
# ---------------------------------------------------------------------------
NESTED_MARKDOWN_PIPELINE = ChunkingPipeline(
    name="nested_markdown",
    splitter=MarkdownBlockSplitter(),
    packer=BlockAwarePacker(BlockAwarePackerConfig(chunk_size=DEFAULT_CHUNK_SIZE, level=ChunkLevel.READ)),
    pre_processors=(MarkdownPreProcessor(),),
    post_processors=(
        ChunkFinalizer(),
        NestedChunkProcessor(NestedChunkConfig(child_chunk_size=600, child_overlap=100)),
    ),
    extra_indexer=ChunkExtraIndexer(),
)

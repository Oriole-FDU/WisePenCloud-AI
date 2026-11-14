"""面向检索场景的 Markdown 结构解析与分块

把一篇 Markdown 文档解析为带源码偏移的结构节点树，识别章节层级、编号题注
等结构事实，并在结构边界处拆分为适合送入模型的检索单元（chunk），同时保证
每个 chunk 可追溯回原文。

处理流程：
    Markdown 原文
      - 解析（Parser）-> list[MarkdownNode]
      - 构建章节与锚点（Chunker） -> list[Section], list[Anchor]
      - 拆分（Splitter）
      - 装箱（ChunkPacker）-> list[MarkdownChunk]
      - 目录（OutlineFormatter）-> 导航文本（可选）
"""

from .chunking import (
    Anchor,
    ChunkingPolicy,
    ChunkPacker,
    MarkdownChunk,
    MarkdownChunker,
    MarkdownChunkingResult,
    Section,
    StructuralNodeSplitter,
    TiktokenTokenCounter,
    TokenCounter,
    default_token_counter,
)
from .outline import OutlineFormatter
from .parsing import MarkdownParser
from .model import MarkdownNode, MarkdownNodeKind, Span

__all__ = [
    "Anchor",
    "ChunkPacker",
    "ChunkingPolicy",
    "MarkdownChunk",
    "MarkdownChunker",
    "MarkdownChunkingResult",
    "MarkdownNode",
    "MarkdownNodeKind",
    "MarkdownParser",
    "OutlineFormatter",
    "Section",
    "Span",
    "StructuralNodeSplitter",
    "TiktokenTokenCounter",
    "TokenCounter",
    "default_token_counter",
]

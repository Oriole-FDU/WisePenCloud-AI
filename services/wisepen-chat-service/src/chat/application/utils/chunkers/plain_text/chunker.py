from __future__ import annotations

from dataclasses import dataclass

from .._utils.chunk_ids import assign_chunk_ids
from .._utils.recursive_splitter import split_plain_text
from ..models import Chunk, ChunkDocument, ChunkerKind, ChunkingResult, SourceSpan


@dataclass(frozen=True, slots=True)
class PlainTextChunkerConfig:
    """纯文本递归分块的尺寸配置。"""

    chunk_size: int = 6000
    chunk_overlap: int = 0

    def __post_init__(self) -> None:
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError("chunk_overlap must be between 0 and chunk_size")


class PlainTextChunker:
    """按语言无关分隔符递归切分纯文本，并保留原文位置。"""

    __slots__ = ("config",)

    def __init__(self, config: PlainTextChunkerConfig | None = None) -> None:
        self.config = config or PlainTextChunkerConfig()

    def chunk(self, *, document: ChunkDocument) -> ChunkingResult:
        """生成无结构 locator 的普通文本分块。"""
        blocks = split_plain_text(
            document,
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
        )
        # source span 让 Tool Content 无需信任 chunk 文本本身即可回读原文。
        chunks = assign_chunk_ids(
            tuple(
                Chunk(
                    chunk_id=f"chunk-{block.block_index}",
                    text=block.text,
                    chunk_index=block.block_index,
                    start_offset=block.start_offset,
                    end_offset=block.end_offset,
                    source_spans=(SourceSpan(block.start_offset, block.end_offset),),
                    start_block=block.block_index,
                    end_block=block.block_index,
                )
                for block in blocks
            )
        )
        return ChunkingResult(
            chunks=chunks,
            blocks=blocks,
            chunker=ChunkerKind.PLAIN_TEXT,
            metadata={
                "block_count": len(blocks),
                "chunk_count": len(chunks),
                "locator_count": 0,
            },
        )

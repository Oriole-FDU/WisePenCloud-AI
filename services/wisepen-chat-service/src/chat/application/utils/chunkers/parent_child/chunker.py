from __future__ import annotations

from dataclasses import dataclass

from .._utils.normalization import normalize_parent_child_chunks
from .._utils.recursive_splitter import split_markdown_text
from ..markdown.chunker import MarkdownChunker, MarkdownChunkerConfig
from ..markdown.locator import build_markdown_locators
from ..models import Chunk, ChunkDocument, ChunkerKind, ChunkRole, ChunkingResult


@dataclass(frozen=True, slots=True)
class ParentChildMarkdownChunkerConfig(MarkdownChunkerConfig):
    """在 Markdown 父块尺寸之上增加子块递归切分配置。"""

    child_chunk_size: int = 600
    child_chunk_overlap: int = 100

    def __post_init__(self) -> None:
        MarkdownChunkerConfig.__post_init__(self)
        if self.child_chunk_size <= 0:
            raise ValueError("child_chunk_size must be positive")
        if not 0 <= self.child_chunk_overlap < self.child_chunk_size:
            raise ValueError(
                "child_chunk_overlap must be between 0 and child_chunk_size"
            )


class ParentChildMarkdownChunker(MarkdownChunker):
    """复用 Markdown 父块与 locator，并派生用于精确检索的子块。"""

    __slots__ = ()

    def __init__(self, config: ParentChildMarkdownChunkerConfig | None = None) -> None:
        super().__init__(config or ParentChildMarkdownChunkerConfig())

    def chunk(self, *, document: ChunkDocument) -> ChunkingResult:
        """先构建 Markdown 父块，再派生用于精确检索的子块。"""
        blocks = self._parser.parse(document.text)
        parents = self._build_structural_chunks(
            blocks=blocks,
            role=ChunkRole.PARENT,
        )
        children = self._derive_children(parents)
        chunks = normalize_parent_child_chunks((*parents, *children))
        locators = build_markdown_locators(
            text_length=len(document.text),
            blocks=blocks,
            chunks=chunks,
        )
        return ChunkingResult(
            chunks=chunks,
            blocks=blocks,
            locators=locators,
            chunker=ChunkerKind.PARENT_CHILD_MARKDOWN,
            metadata={
                "block_count": len(blocks),
                "chunk_count": len(chunks),
                "locator_count": len(locators),
            },
        )

    def _derive_children(self, parents: tuple[Chunk, ...]) -> tuple[Chunk, ...]:
        """在每个父块内部递归切分，并换算回整篇原文的 offset。"""
        config = self.config

        children: list[Chunk] = []
        for parent in parents:
            blocks = split_markdown_text(
                ChunkDocument(text=parent.text),
                chunk_size=config.child_chunk_size,
                chunk_overlap=config.child_chunk_overlap,
            )
            if len(blocks) <= 1:
                continue

            page_label = parent.metadata.get("page_label")
            for child_index, block in enumerate(blocks):
                children.append(
                    Chunk(
                        chunk_id=f"{parent.chunk_id}:child:{child_index}",
                        text=block.text,
                        chunk_index=0,
                        role=ChunkRole.CHILD,
                        parent_chunk_id=parent.chunk_id,
                        start_offset=parent.start_offset + block.start_offset,
                        end_offset=parent.start_offset + block.end_offset,
                        metadata={
                            "child_index": child_index,
                            "child_count": len(blocks),
                            **({"page_label": page_label} if page_label else {}),
                        },
                    )
                )
        return tuple(children)

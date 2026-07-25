from __future__ import annotations

from dataclasses import dataclass, replace

from .locator import build_markdown_locators
from .parser import MarkdownParser
from .._utils.chunk_ids import assign_chunk_ids
from .._utils.recursive_splitter import split_markdown_text
from ..models import (
    BlockKind,
    Chunk,
    ChunkDocument,
    ChunkerKind,
    ChunkingResult,
    MarkdownChunkingStrategy,
    SourceSpan,
    TextBlock,
)


@dataclass(frozen=True, slots=True)
class MarkdownChunkerConfig:
    """Markdown 分块策略与字符窗口。"""

    strategy: MarkdownChunkingStrategy = MarkdownChunkingStrategy.AUTO
    max_characters: int = 6000
    new_after_n_chars: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.strategy, MarkdownChunkingStrategy):
            raise TypeError("strategy must be a MarkdownChunkingStrategy")
        if self.max_characters <= 0:
            raise ValueError("max_characters must be positive")
        if self.new_after_n_chars is not None and self.new_after_n_chars < 0:
            raise ValueError("new_after_n_chars must not be negative")

    @property
    def soft_limit(self) -> int:
        if self.new_after_n_chars is None:
            return self.max_characters
        return min(self.new_after_n_chars, self.max_characters)


class MarkdownChunker:
    """按显式策略把 Markdown 结构块投影为检索块。"""

    __slots__ = ("config", "_parser")

    def __init__(self, config: MarkdownChunkerConfig | None = None) -> None:
        self.config = config or MarkdownChunkerConfig()
        self._parser = MarkdownParser()

    def chunk(self, *, document: ChunkDocument) -> ChunkingResult:
        blocks = self._parser.parse(document.text)
        strategy = self._resolve_strategy(blocks)

        if strategy is MarkdownChunkingStrategy.BY_PAGE:
            chunks = self._chunk_by_page(blocks)
        else:
            chunks = self._chunk_by_title(blocks)

        chunks = assign_chunk_ids(chunks)
        locators = build_markdown_locators(
            text_length=len(document.text),
            blocks=blocks,
            chunks=chunks,
        )
        return ChunkingResult(
            chunks=chunks,
            blocks=blocks,
            locators=locators,
            chunker=ChunkerKind.MARKDOWN,
            metadata={
                "strategy": strategy.value,
                "block_count": len(blocks),
                "chunk_count": len(chunks),
                "locator_count": len(locators),
            },
        )

    def _resolve_strategy(
        self,
        blocks: tuple[TextBlock, ...],
    ) -> MarkdownChunkingStrategy:
        strategy = self.config.strategy
        has_pages = any(block.block_kind is BlockKind.PAGE_MARKER for block in blocks)
        if strategy is MarkdownChunkingStrategy.AUTO:
            return (
                MarkdownChunkingStrategy.BY_PAGE
                if has_pages
                else MarkdownChunkingStrategy.BY_TITLE
            )
        if strategy is MarkdownChunkingStrategy.BY_PAGE and not has_pages:
            raise ValueError("by_page strategy requires page markers")
        return strategy

    def _chunk_by_page(
        self,
        blocks: tuple[TextBlock, ...],
    ) -> tuple[Chunk, ...]:
        chunks: list[Chunk] = []
        page_blocks: list[TextBlock] = []

        def flush_page() -> None:
            if not page_blocks:
                return
            chunks.extend(
                self._pack_blocks(
                    tuple(page_blocks),
                    soft_limit=self.config.max_characters,
                )
            )
            page_blocks.clear()

        for block in blocks:
            if block.block_kind is BlockKind.PAGE_MARKER:
                flush_page()
                continue
            page_blocks.append(block)

        flush_page()
        return tuple(chunks)

    def _chunk_by_title(
        self,
        blocks: tuple[TextBlock, ...],
    ) -> tuple[Chunk, ...]:
        chunks: list[Chunk] = []
        section_blocks: list[TextBlock] = []
        section_has_body = False

        def flush_section() -> None:
            nonlocal section_has_body
            if not section_blocks:
                return
            chunks.extend(
                self._pack_blocks(
                    tuple(section_blocks),
                    soft_limit=self.config.soft_limit,
                )
            )
            section_blocks.clear()
            section_has_body = False

        for block in blocks:
            if block.block_kind is BlockKind.PAGE_MARKER:
                continue
            if block.block_kind is BlockKind.HEADING and section_has_body:
                flush_section()
            section_blocks.append(block)
            if block.block_kind is not BlockKind.HEADING:
                section_has_body = True

        flush_section()
        return tuple(chunks)

    def _pack_blocks(
        self,
        blocks: tuple[TextBlock, ...],
        *,
        soft_limit: int,
    ) -> tuple[Chunk, ...]:
        chunks: list[Chunk] = []
        selected: list[TextBlock] = []
        selected_chars = 0

        def flush() -> None:
            nonlocal selected_chars
            if not selected:
                return
            chunks.append(self._build_chunk(tuple(selected)))
            selected.clear()
            selected_chars = 0

        for block in blocks:
            for part in self._split_oversized_block(block):
                separator_chars = 2 if selected else 0
                part_chars = len(part.text) + separator_chars
                if selected and (
                    selected_chars >= soft_limit
                    or selected_chars + part_chars > self.config.max_characters
                ):
                    flush()
                    separator_chars = 0
                    part_chars = len(part.text)

                selected.append(part)
                selected_chars += part_chars

        flush()
        return tuple(chunks)

    def _split_oversized_block(
        self,
        block: TextBlock,
    ) -> tuple[TextBlock, ...]:
        if len(block.text) <= self.config.max_characters:
            return (block,)

        parts = split_markdown_text(
            ChunkDocument(text=block.text),
            chunk_size=self.config.max_characters,
            chunk_overlap=0,
        )
        return tuple(
            replace(
                block,
                block_id=f"{block.block_id}:part:{index}",
                text=part.text,
                start_offset=(
                    block.start_offset + part.start_offset
                    if block.start_offset is not None and part.start_offset is not None
                    else None
                ),
                end_offset=(
                    block.start_offset + part.end_offset
                    if block.start_offset is not None and part.end_offset is not None
                    else None
                ),
            )
            for index, part in enumerate(parts)
        )

    @staticmethod
    def _build_chunk(selected: tuple[TextBlock, ...]) -> Chunk:
        block_kinds = tuple(block.block_kind for block in selected)
        section_paths = tuple(
            dict.fromkeys(
                block.section_path for block in selected if block.section_path
            )
        )
        page_labels = tuple(
            dict.fromkeys(
                str(page_label)
                for block in selected
                if (page_label := block.metadata.get("page_label")) is not None
            )
        )
        titles = tuple(
            str(title)
            for block in selected
            if (title := block.metadata.get("title")) is not None
        )
        source_spans = tuple(
            SourceSpan(block.start_offset, block.end_offset)
            for block in selected
            if block.start_offset is not None and block.end_offset is not None
        )
        return Chunk(
            chunk_id="pending",
            text="\n\n".join(block.text for block in selected if block.text).strip(),
            chunk_index=0,
            start_offset=source_spans[0].start_offset if source_spans else None,
            end_offset=source_spans[-1].end_offset if source_spans else None,
            source_spans=source_spans,
            start_block=selected[0].block_index,
            end_block=selected[-1].block_index,
            metadata={
                "block_kinds": block_kinds,
                "section_paths": section_paths,
                "page_labels": page_labels,
                **({"titles": titles} if titles else {}),
            },
        )

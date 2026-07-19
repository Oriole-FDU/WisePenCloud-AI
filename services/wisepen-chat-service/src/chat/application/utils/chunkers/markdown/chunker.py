from __future__ import annotations

from dataclasses import dataclass

from .locator import build_markdown_locators
from .parser import MarkdownParser
from .._utils.normalization import normalize_flat_chunks
from ..models import (
    BlockKind,
    Chunk,
    ChunkDocument,
    ChunkerKind,
    ChunkRole,
    ChunkingResult,
    TextBlock,
)


@dataclass(frozen=True, slots=True)
class MarkdownChunkerConfig:
    """Markdown 结构块聚合的目标尺寸。"""

    chunk_size: int = 6000

    def __post_init__(self) -> None:
        if self.chunk_size <= 0:
            raise ValueError("chunk_size must be positive")


class MarkdownChunker:
    """按 Markdown 结构聚合正文，并构建章节、页码和锚点定位。"""

    __slots__ = ("config", "_parser")

    def __init__(self, config: MarkdownChunkerConfig | None = None) -> None:
        self.config = config or MarkdownChunkerConfig()
        self._parser = MarkdownParser()

    def chunk(self, *, document: ChunkDocument) -> ChunkingResult:
        """执行结构解析、聚合、归一化和语义定位。"""
        blocks = self._parser.parse(document.text)
        chunks = self._build_structural_chunks(
            blocks=blocks,
            role=ChunkRole.FLAT,
        )
        chunks = normalize_flat_chunks(chunks)
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
                "block_count": len(blocks),
                "chunk_count": len(chunks),
                "locator_count": len(locators),
            },
        )

    def _build_structural_chunks(
            self,
            *,
            blocks: tuple[TextBlock, ...],
            role: ChunkRole,
    ) -> tuple[Chunk, ...]:
        """按结构块聚合 chunk；页码标记是不可跨越的硬边界。

        单个 TABLE、FORMULA、IMAGE 等结构块不会因尺寸超限被拆开，
        从而保证 anchor 始终可以绑定到一个完整包含该结构块的 chunk。
        """
        chunks: list[Chunk] = []
        selected: list[TextBlock] = []
        selected_chars = 0
        active_page_label: str | None = None

        def flush() -> None:
            """冻结当前页内已选结构块，并开始下一组聚合。"""
            nonlocal selected_chars
            if not selected:
                return

            chunks.append(
                self._build_chunk(
                    selected=tuple(selected),
                    chunk_index=len(chunks),
                    role=role,
                    page_label=active_page_label,
                )
            )
            selected.clear()
            selected_chars = 0

        for block in blocks:
            if block.block_kind == BlockKind.PAGE_MARKER:
                flush()
                active_page_label = str(block.metadata["page_label"])
                continue

            if selected and selected_chars + len(block.text) > self.config.chunk_size:
                flush()
            selected.append(block)
            selected_chars += len(block.text)

        flush()
        return tuple(chunks)

    @staticmethod
    def _build_chunk(
            *,
            selected: tuple[TextBlock, ...],
            chunk_index: int,
            role: ChunkRole,
            page_label: str | None,
    ) -> Chunk:
        """将一组连续结构块投影为一个最终 chunk 的初始形态。"""
        block_kinds = tuple(block.block_kind for block in selected)
        section_paths = tuple(
            dict.fromkeys(
                block.section_path for block in selected if block.section_path
            )
        )
        titles = tuple(
            str(title)
            for title in (
                block.metadata.get("title")
                for block in selected
                if block.metadata.get("title")
            )
        )
        return Chunk(
            chunk_id=f"chunk-{chunk_index}",
            text="\n\n".join(block.text for block in selected if block.text).strip(),
            chunk_index=chunk_index,
            role=role,
            start_offset=selected[0].start_offset,
            end_offset=selected[-1].end_offset,
            start_block=selected[0].block_index,
            end_block=selected[-1].block_index,
            metadata={
                "block_kinds": block_kinds,
                "section_paths": section_paths,
                **({"titles": titles} if titles else {}),
                **({"page_label": page_label} if page_label else {}),
            },
        )

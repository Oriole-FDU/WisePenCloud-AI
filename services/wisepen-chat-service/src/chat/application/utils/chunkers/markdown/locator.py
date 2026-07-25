from __future__ import annotations

import re

from ..models import BlockKind, Chunk, ChunkLocator, LocatorKind, TextBlock

_TABLE_RE = re.compile(r"^(?:Table|表格|表)\s+(\d+(?:\.\d+)*)", re.IGNORECASE)
_FORMULA_RE = re.compile(
    r"(?:Equation|Eq\.?|公式)\s+\(?(\d+(?:\.\d+)*)\)?",
    re.IGNORECASE,
)
_FIGURE_RE = re.compile(r"^(?:Figure|Fig\.?|图)\s+(\d+(?:\.\d+)*)", re.IGNORECASE)


def build_markdown_locators(
    *,
    text_length: int,
    blocks: tuple[TextBlock, ...],
    chunks: tuple[Chunk, ...],
) -> tuple[ChunkLocator, ...]:
    """基于最终 chunk 构建章节、页码和锚点三类定位。"""
    return (
        *_section_locators(blocks, chunks, text_length),
        *_page_locators(blocks, chunks, text_length),
        *_anchor_locators(blocks, chunks),
    )


def _section_locators(
    blocks: tuple[TextBlock, ...],
    chunks: tuple[Chunk, ...],
    text_length: int,
) -> tuple[ChunkLocator, ...]:
    """章节范围包含标题本身，并延伸到下一个标题之前。"""
    headings = [
        block
        for block in blocks
        if block.block_kind == BlockKind.HEADING
        and block.section_path
        and block.start_offset is not None
    ]
    locators: list[ChunkLocator] = []
    for index, heading in enumerate(headings):
        end_offset = (
            headings[index + 1].start_offset
            if index + 1 < len(headings)
            else text_length
        )
        covered = _overlapping_chunks(chunks, heading.start_offset, end_offset)
        if not covered:
            continue

        section_path = " > ".join(heading.section_path)
        locators.append(
            ChunkLocator(
                name=f"section:{section_path}",
                kind=LocatorKind.SECTION,
                chunk_indices=tuple(chunk.chunk_index for chunk in covered),
                chunk_ids=tuple(chunk.chunk_id for chunk in covered),
                start_offset=heading.start_offset,
                end_offset=end_offset,
                metadata={"section_path": heading.section_path},
            )
        )
    return tuple(locators)


def _page_locators(
    blocks: tuple[TextBlock, ...],
    chunks: tuple[Chunk, ...],
    text_length: int,
) -> tuple[ChunkLocator, ...]:
    """页定位保留 marker 范围，但关联 chunk 时排除 marker 本身。"""
    markers = [
        block
        for block in blocks
        if block.block_kind == BlockKind.PAGE_MARKER
        and block.start_offset is not None
        and block.end_offset is not None
        and block.metadata.get("page_label") is not None
    ]
    locators: list[ChunkLocator] = []
    for index, marker in enumerate(markers):
        end_offset = (
            markers[index + 1].start_offset if index + 1 < len(markers) else text_length
        )
        covered = _overlapping_chunks(chunks, marker.end_offset, end_offset)
        if not covered:
            continue

        page_label = str(marker.metadata["page_label"])
        locators.append(
            ChunkLocator(
                name=f"page:{page_label}",
                kind=LocatorKind.PAGE,
                chunk_indices=tuple(chunk.chunk_index for chunk in covered),
                chunk_ids=tuple(chunk.chunk_id for chunk in covered),
                start_offset=marker.start_offset,
                end_offset=end_offset,
                metadata={"page_label": page_label},
            )
        )
    return tuple(locators)


def _anchor_locators(
    blocks: tuple[TextBlock, ...],
    chunks: tuple[Chunk, ...],
) -> tuple[ChunkLocator, ...]:
    """将结构原子块的锚点绑定到单个所属 chunk。"""
    locators: list[ChunkLocator] = []
    for block in blocks:
        anchor_label = _anchor_label(block)
        if anchor_label is None:
            continue
        if block.start_offset is None or block.end_offset is None:
            continue
        covered = _overlapping_chunks(
            chunks,
            block.start_offset,
            block.end_offset,
        )
        if not covered:
            continue

        locators.append(
            ChunkLocator(
                name=f"anchor:{anchor_label}",
                kind=LocatorKind.ANCHOR,
                chunk_indices=tuple(chunk.chunk_index for chunk in covered),
                chunk_ids=tuple(chunk.chunk_id for chunk in covered),
                start_offset=block.start_offset,
                end_offset=block.end_offset,
                metadata={"anchor_label": anchor_label},
            )
        )
    return tuple(locators)


def _anchor_label(block: TextBlock) -> str | None:
    """优先读取 parser 产出的锚点，再按结构类型从正文提取。"""
    stored = block.metadata.get("anchor_label")
    if isinstance(stored, str) and stored:
        return stored

    if block.block_kind == BlockKind.TABLE:
        match = _TABLE_RE.match(block.text.strip())
        prefix = "Table"
    elif block.block_kind == BlockKind.FORMULA:
        match = _FORMULA_RE.search(block.text)
        prefix = "Equation"
    elif block.block_kind == BlockKind.IMAGE:
        match = _FIGURE_RE.match(str(block.metadata.get("alt", "")).strip())
        prefix = "Figure"
    else:
        return None

    return f"{prefix} {match.group(1)}" if match else None


def _overlapping_chunks(
    chunks: tuple[Chunk, ...],
    start_offset: int,
    end_offset: int,
) -> tuple[Chunk, ...]:
    """查找与半开区间 `[start_offset, end_offset)` 相交的 chunks。"""
    return tuple(
        chunk
        for chunk in chunks
        if any(
            span.start_offset < end_offset and span.end_offset > start_offset
            for span in chunk.source_spans
        )
    )

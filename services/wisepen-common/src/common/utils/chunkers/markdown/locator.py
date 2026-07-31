from __future__ import annotations

from ..models import BlockKind, Chunk, ChunkLocator, LocatorKind, TextBlock


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
    """章节范围包含标题本身，并延伸到下一个同级/更高级标题之前。

    例如：H1 范围从 H1 起始到下一个 H1；H2 范围从 H2 起始到下一个同级 H2。
    """
    headings = [
        block
        for block in blocks
        if block.block_kind == BlockKind.HEADING
        and block.section_path
        and block.start_offset is not None
    ]
    locators: list[ChunkLocator] = []
    for index, heading in enumerate(headings):
        heading_level = int(heading.metadata["heading_level"])
        end_offset = text_length
        for candidate in headings[index + 1 :]:
            candidate_level = int(candidate.metadata["heading_level"])
            if candidate_level <= heading_level:
                end_offset = candidate.start_offset
                break
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
    """页定位保留 marker 范围，但关联 chunk 时排除 marker 本身。

    关键点：查找覆盖 chunk 时使用 marker.end_offset（而非 start_offset），
    因为 PAGE_MARKER 自身的 offset 不属于任何 chunk，从 marker 结束处开始
    才能正确匹配该页的第一个 chunk。
    """
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
    """将结构块锚点映射到覆盖它的 chunks。"""
    locators: list[ChunkLocator] = []
    for block in blocks:
        anchor_label = block.metadata.get("anchor_label")
        if not isinstance(anchor_label, str) or not anchor_label:
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


def _overlapping_chunks(
    chunks: tuple[Chunk, ...],
    start_offset: int,
    end_offset: int,
) -> tuple[Chunk, ...]:
    """查找与半开区间 `[start_offset, end_offset)` 相交的 chunks。

    使用 chunk.source_spans 而非 chunk.start/end_offset 进行相交判断，
    因为 chunk 的 outer offset 可能包含被省略的 page marker，
    span 才是 chunk 在原文中的实际证据范围。
    """
    return tuple(
        chunk
        for chunk in chunks
        if any(
            span.start_offset < end_offset and span.end_offset > start_offset
            for span in chunk.source_spans
        )
    )

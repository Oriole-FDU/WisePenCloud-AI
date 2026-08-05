from __future__ import annotations

from ..models import BlockKind, LocatorKind, TextBlock, TextLocator


def build_markdown_locators(
    *,
    text_length: int,
    blocks: tuple[TextBlock, ...],
) -> tuple[TextLocator, ...]:
    """基于 Markdown 结构块构建章节、页码和锚点原文定位。"""
    return (
        *_section_locators(blocks, text_length),
        *_page_locators(blocks, text_length),
        *_anchor_locators(blocks),
    )


def _section_locators(
    blocks: tuple[TextBlock, ...],
    text_length: int,
) -> tuple[TextLocator, ...]:
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
    locators: list[TextLocator] = []
    for index, heading in enumerate(headings):
        heading_level = int(heading.metadata["heading_level"])
        end_offset = text_length
        for candidate in headings[index + 1 :]:
            candidate_level = int(candidate.metadata["heading_level"])
            if candidate_level <= heading_level:
                end_offset = candidate.start_offset
                break
        section_path = " > ".join(heading.section_path)
        locators.append(
            TextLocator(
                name=f"section:{section_path}",
                kind=LocatorKind.SECTION,
                start_offset=heading.start_offset,
                end_offset=end_offset,
            )
        )
    return tuple(locators)


def _page_locators(
    blocks: tuple[TextBlock, ...],
    text_length: int,
) -> tuple[TextLocator, ...]:
    """每个页定位从当前 marker 开始，到下一个 marker 之前结束。"""
    markers = [
        block
        for block in blocks
        if block.block_kind == BlockKind.PAGE_MARKER
        and block.start_offset is not None
        and block.end_offset is not None
        and block.metadata.get("page_label") is not None
    ]
    locators: list[TextLocator] = []
    for index, marker in enumerate(markers):
        end_offset = (
            markers[index + 1].start_offset if index + 1 < len(markers) else text_length
        )
        page_label = str(marker.metadata["page_label"])
        locators.append(
            TextLocator(
                name=f"page:{page_label}",
                kind=LocatorKind.PAGE,
                start_offset=marker.start_offset,
                end_offset=end_offset,
            )
        )
    return tuple(locators)


def _anchor_locators(
    blocks: tuple[TextBlock, ...],
) -> tuple[TextLocator, ...]:
    """将 parser 识别出的结构锚点保留为精确原文范围。"""
    locators: list[TextLocator] = []
    for block in blocks:
        anchor_label = block.metadata.get("anchor_label")
        if not isinstance(anchor_label, str) or not anchor_label:
            continue
        if block.start_offset is None or block.end_offset is None:
            continue
        locators.append(
            TextLocator(
                name=f"anchor:{anchor_label}",
                kind=LocatorKind.ANCHOR,
                start_offset=block.start_offset,
                end_offset=block.end_offset,
            )
        )
    return tuple(locators)

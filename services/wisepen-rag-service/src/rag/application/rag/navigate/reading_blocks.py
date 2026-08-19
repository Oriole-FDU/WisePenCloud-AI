"""将可回源的 ReadingBlock 投影为带 Section 边界的公开阅读文本。"""

from dataclasses import dataclass, field

from common.utils.document import Section, SourceSpan

from rag.domain.models.content import ReadingBlock


@dataclass(slots=True)
class ReadingBlockSectionView:
    """一个公开 ReadingBlock 中某个 Section 的元数据与覆盖状态。"""

    section_id: str
    title: str
    section_path: str
    # 仅表示当前 ReadingBlock 是否覆盖该 Section 的全部直属正文。
    block_is_enough: bool


@dataclass(slots=True)
class ReadingBlockPresentation:
    """公开 ReadingBlock 的单一展示文本及其有序 Section 组成。"""

    text: str
    sections: list[ReadingBlockSectionView] = field(default_factory=list)


def present_reading_block(
    block: ReadingBlock,
    sections: list[Section],
) -> ReadingBlockPresentation:
    """为多 Section 父块补标题边界，但不改写可回源的 raw_text。"""
    if [section.section_id for section in sections] != block.section_ids:
        raise ValueError(f"reading block {block.block_id} has invalid section metadata")

    span_texts = _span_texts(block)
    spans_by_section = {section.section_id: [] for section in sections}
    texts_by_section = {section.section_id: [] for section in sections}
    for source_span, source_text in span_texts:
        section = _section_for_span(source_span, sections)
        if section is None:
            raise ValueError(
                f"reading block {block.block_id} has span outside its Sections"
            )
        spans_by_section[section.section_id].append(source_span)
        texts_by_section[section.section_id].append(source_text)

    section_views = [
        ReadingBlockSectionView(
            section_id=section.section_id,
            title=section.title,
            section_path=" > ".join(section.section_path),
            block_is_enough=_spans_cover(
                spans_by_section[section.section_id],
                section.content_spans,
            ),
        )
        for section in sections
    ]
    if len(sections) == 1:
        return ReadingBlockPresentation(text=block.raw_text, sections=section_views)

    # 标题只属于公开阅读投影：检索、图谱证据和原文坐标继续使用原始 raw_text。
    text = "\n\n".join(
        f"{_heading(section)}\n\n" + "\n\n".join(texts_by_section[section.section_id])
        for section in sections
    )
    return ReadingBlockPresentation(text=text, sections=section_views)


def _span_texts(block: ReadingBlock) -> list[tuple[SourceSpan, str]]:
    """按 raw_text 的渲染规则还原每个原文 span 对应的展示文本。"""
    cursor = 0
    span_texts: list[tuple[SourceSpan, str]] = []
    for index, source_span in enumerate(block.source_spans):
        length = source_span.end_offset - source_span.start_offset
        span_texts.append((source_span, block.raw_text[cursor : cursor + length]))
        cursor += length
        if index + 1 < len(block.source_spans):
            cursor += 2
    return span_texts


def _section_for_span(source_span: SourceSpan, sections: list[Section]) -> Section | None:
    for section in sections:
        if any(
            source_span.start_offset >= content_span.start_offset
            and source_span.end_offset <= content_span.end_offset
            for content_span in section.content_spans
        ):
            return section
    return None


def _heading(section: Section) -> str:
    level = min(max(section.level, 1), 6)
    title = section.title or section.section_id
    return f"{'#' * level} {title}"


def _spans_cover(covered: list[SourceSpan], target: list[SourceSpan]) -> bool:
    """判断当前父块在某个 Section 内的原文区间是否完整覆盖直属正文。"""
    if not target:
        return True
    merged: list[list[int]] = []
    for span in sorted(covered, key=lambda item: item.start_offset):
        if merged and span.start_offset <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], span.end_offset)
        else:
            merged.append([span.start_offset, span.end_offset])
    return all(
        any(start <= span.start_offset and span.end_offset <= end for start, end in merged)
        for span in target
    )

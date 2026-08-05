from __future__ import annotations

from rag.utils.chunkers import SourceSpan
from rag.utils.xml_markup import xml_attr, xml_cdata

from .models import (
    KnowledgeExtractionSource,
    KnowledgeExtractionWindow,
    KnowledgeWindowSourceSpan,
)

# 邻接父块上下文长度，仅用于边界消歧，不参与事实抽取。
_ADJACENT_CONTEXT_CHARS = 800
_PARENT_WINDOW_CHARS = 6000
_PARENT_WINDOW_OVERLAP_CHARS = 2400


def build_extraction_windows(
        source: KnowledgeExtractionSource,
) -> tuple[KnowledgeExtractionWindow, ...]:
    """将 RAG 内容投影转换为知识抽取窗口。"""
    windows: list[KnowledgeExtractionWindow] = []

    blocks = source.blocks
    for index, block in enumerate(blocks):
        source_refs = tuple(
            source_ref
            for source_ref in source.source_refs
            if _spans_overlap(block.source_spans, source_ref.source_spans)
        )

        # 无文本或无 source 定位信息的父块无法提供可靠 evidence，跳过。
        if not block.raw_text.strip() or not source_refs:
            continue

        previous_block = blocks[index - 1] if index > 0 else None
        next_block = (
            blocks[index + 1] if index + 1 < len(blocks) else None
        )
        parent_mappings: list[KnowledgeWindowSourceSpan] = []
        search_start = 0
        for source_span in block.source_spans:
            source_text = source.markdown[source_span.start_offset: source_span.end_offset]
            local_start = block.raw_text.find(source_text, search_start)
            if not source_text or local_start < 0:
                raise ValueError(f"reading block {block.block_id} source span does not match raw text")
            local_end = local_start + len(source_text)
            parent_mappings.append(
                KnowledgeWindowSourceSpan(
                    local_start=local_start,
                    local_end=local_end,
                    source_start=source_span.start_offset,
                    source_end=source_span.end_offset,
                )
            )
            search_start = local_end

        for window_index, (window_start, window_end) in enumerate(_parent_window_ranges(block.raw_text)):
            window_id = (
                block.block_id
                if window_start == 0 and window_end == len(block.raw_text)
                else f"{block.block_id}:window:{window_index}"
            )
            windows.append(
                KnowledgeExtractionWindow(
                    resource_id=source.resource_id,
                    document_title=source.document_title,
                    document_version=source.document_version,
                    content_revision=source.content_revision,
                    parent_id=block.block_id,
                    parent_index=block.block_index,
                    window_id=window_id,
                    window_index=window_index,
                    # 当前执行窗口文本；parent_id 仍是抽取归属边界。
                    current_text=block.raw_text[window_start:window_end],
                    # 保留原始定位信息，方便实体/关系 evidence 回溯。
                    source_mappings=_clip_mappings(
                        tuple(parent_mappings),
                        window_start=window_start,
                        window_end=window_end,
                    ),
                    source_refs=source_refs,
                    section_paths=(block.section_path,) if block.section_path else (),
                    # 只提供同 section 相邻父块边界文本，兜住跨父块开头/结尾的指代消歧。
                    previous_context=(
                        previous_block.raw_text[-_ADJACENT_CONTEXT_CHARS:]
                        if previous_block is not None
                        and previous_block.section_id == block.section_id
                        and window_index == 0
                        else ""
                    ),
                    next_context=(
                        next_block.raw_text[:_ADJACENT_CONTEXT_CHARS]
                        if next_block is not None
                        and next_block.section_id == block.section_id
                        and window_end == len(block.raw_text)
                        else ""
                    ),
                )
            )

    return tuple(windows)


def _parent_window_ranges(text: str) -> tuple[tuple[int, int], ...]:
    if len(text) <= _PARENT_WINDOW_CHARS:
        return ((0, len(text)),)

    ranges: list[tuple[int, int]] = []
    step = _PARENT_WINDOW_CHARS - _PARENT_WINDOW_OVERLAP_CHARS
    start = 0
    while start < len(text):
        end = min(start + _PARENT_WINDOW_CHARS, len(text))
        ranges.append((start, end))
        if end == len(text):
            break
        start += step
    return tuple(ranges)


def _clip_mappings(
        mappings: tuple[KnowledgeWindowSourceSpan, ...],
        *,
        window_start: int,
        window_end: int,
) -> tuple[KnowledgeWindowSourceSpan, ...]:
    clipped: list[KnowledgeWindowSourceSpan] = []
    for mapping in mappings:
        local_start = max(mapping.local_start, window_start)
        local_end = min(mapping.local_end, window_end)
        if local_start >= local_end:
            continue
        clipped.append(
            KnowledgeWindowSourceSpan(
                local_start=local_start - window_start,
                local_end=local_end - window_start,
                source_start=mapping.source_start + local_start - mapping.local_start,
                source_end=mapping.source_start + local_end - mapping.local_start,
            )
        )
    return tuple(clipped)


def _spans_overlap(left: tuple[SourceSpan, ...], right: tuple[SourceSpan, ...]) -> bool:
    return any(
        left_span.start_offset < right_span.end_offset
        and left_span.end_offset > right_span.start_offset
        for left_span in left
        for right_span in right
    )


def render_extraction_window(window: KnowledgeExtractionWindow) -> str:
    """将知识抽取窗口渲染为 LLM 输入。"""
    section_paths = "\n".join(
        f"      <section_path>{xml_cdata(' > '.join(path))}</section_path>"
        for path in window.section_paths
    ) or f"      <section_path>{xml_cdata('(document root)')}</section_path>"

    return f"""EXTRACTION_RULES:
- Extract general entities and explicit cross-document relations.
- Extract only facts supported by <current_parent>.
- evidence_quote must be one exact continuous substring of <current_parent>.
- Context before and after <current_parent> is only for disambiguation.
- Use only node types, entity types, relation types and endpoint directions from the schema.
- Use <current_resource> as the Resource node and copy its resource_id exactly.
- RELATED_TO requires a specific predicate.
- Return no node or relation when evidence is insufficient.

<extraction_window>
  <current_resource resource_id="{xml_attr(window.resource_id)}">
    <document_title>{xml_cdata(window.document_title or window.resource_id)}</document_title>
    <section_paths>
{section_paths}
    </section_paths>
  </current_resource>
  <previous_context>{xml_cdata(window.previous_context)}</previous_context>
  <current_parent>{xml_cdata(window.current_text)}</current_parent>
  <next_context>{xml_cdata(window.next_context)}</next_context>
</extraction_window>
"""

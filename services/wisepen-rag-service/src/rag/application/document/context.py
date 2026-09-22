"""为文档级 LLM 任务构造 target 原位标记的上下文。"""

from collections.abc import Iterable, Sequence

from common.utils.document import SourceSpan

from rag.application.document.models import DocChunk, Document

_SECTION_CONTEXT_LIMIT = 4_000
_WINDOW_CHUNK_STEPS = 2


def build_inline_document_context(
    document: Document,
    chunks: Sequence[DocChunk],
    target: DocChunk,
) -> str:
    """优先使用直属 Section，超长或 flat 文档退化为邻近 Chunk 窗口。

    target 的实际覆盖由每个 ``source_span`` 决定。``chunk_span`` 只是包络区间，
    对非连续来源会包含不属于 target 的间隙，不能用于正文扣除。
    """
    if target.section_id is not None:
        section = next(
            (
                section
                for section in document.structure.sections
                if section.section_id == target.section_id
            ),
            None,
        )
        if section is not None:
            section_context = _render_inline_context(
                document,
                target,
                (*section.content_spans, *target.source_spans),
            )
            if len(section_context) <= _SECTION_CONTEXT_LIMIT:
                return section_context

    ordered_chunks = sorted(chunks, key=lambda chunk: chunk.chunk_index)
    target_index = next(
        (
            index
            for index, chunk in enumerate(ordered_chunks)
            if chunk.chunk_id == target.chunk_id
        ),
        None,
    )
    if target_index is None:
        raise ValueError("target chunk is not present in the document revision")

    start = max(0, target_index - _WINDOW_CHUNK_STEPS)
    end = target_index + _WINDOW_CHUNK_STEPS + 1
    window_spans = (
        span
        for chunk in ordered_chunks[start:end]
        for span in chunk.source_spans
    )
    return _render_inline_context(document, target, window_spans)


def _render_inline_context(
    document: Document,
    target: DocChunk,
    context_spans: Iterable[SourceSpan],
) -> str:
    """按原文 offset 渲染上下文，并从 surrounding text 中扣除 target 实际 spans。"""
    selected_spans = _merge_spans(context_spans)
    target_spans = _merge_spans(target.source_spans)
    if not target_spans:
        raise ValueError("target chunk requires source spans")

    surrounding_spans = _subtract_spans(selected_spans, target_spans)
    first_target_start = target_spans[0].start_offset
    last_target_end = target_spans[-1].end_offset
    if any(
        first_target_start < span.start_offset < last_target_end
        for span in surrounding_spans
    ):
        # 一个 target 标签无法表示内部夹有其他正文的来源；拒绝用包络区间吞掉它。
        raise ValueError("target source spans contain non-target document content")

    before = _source_text(
        document,
        [span for span in surrounding_spans if span.end_offset <= first_target_start],
    )
    after = _source_text(
        document,
        [span for span in surrounding_spans if span.start_offset >= last_target_end],
    )

    title_path = " > ".join(target.section_path) or "文档根"
    body = ["标题路径: " + title_path]
    if before:
        body.append(f"<context_chunk>\n{before}\n</context_chunk>")
    body.append(f"<target_chunk>\n{target.raw_text}\n</target_chunk>")
    if after:
        body.append(f"<context_chunk>\n{after}\n</context_chunk>")
    return "<document_context>\n" + "\n\n".join(body) + "\n</document_context>"


def _merge_spans(spans: Iterable[SourceSpan]) -> list[SourceSpan]:
    """合并重叠或相接的精确源区间，消除 oversized chunk overlap。"""
    ordered = sorted(spans, key=lambda span: (span.start_offset, span.end_offset))
    merged: list[SourceSpan] = []
    for span in ordered:
        if span.length == 0:
            continue
        if not merged or merged[-1].end_offset < span.start_offset:
            merged.append(span)
            continue
        previous = merged[-1]
        merged[-1] = SourceSpan(
            previous.start_offset,
            max(previous.end_offset, span.end_offset),
        )
    return merged


def _subtract_spans(
    source_spans: Sequence[SourceSpan],
    excluded_spans: Sequence[SourceSpan],
) -> list[SourceSpan]:
    """从已排序源区间中逐段扣除 target 的实际覆盖范围。"""
    remaining: list[SourceSpan] = []
    for source in source_spans:
        cursor = source.start_offset
        for excluded in excluded_spans:
            if excluded.end_offset <= cursor:
                continue
            if excluded.start_offset >= source.end_offset:
                break
            if cursor < excluded.start_offset:
                remaining.append(
                    SourceSpan(cursor, min(excluded.start_offset, source.end_offset))
                )
            cursor = max(cursor, excluded.end_offset)
            if cursor >= source.end_offset:
                break
        if cursor < source.end_offset:
            remaining.append(SourceSpan(cursor, source.end_offset))
    return remaining


def _source_text(document: Document, spans: Sequence[SourceSpan]) -> str:
    return "\n\n".join(
        text
        for span in spans
        if (text := document.raw_content[span.start_offset : span.end_offset].strip())
    )

from __future__ import annotations

from chat.application.rag.ingestion import RagSourceRef
from .models import (
    KnowledgeExtractionSource,
    KnowledgeExtractionWindow,
    KnowledgeWindowSourceSpan,
)

# 邻接 chunk 上下文长度，仅用于消歧，不参与事实抽取。
_ADJACENT_CONTEXT_CHARS = 800


def build_extraction_windows(
        source: KnowledgeExtractionSource,
) -> tuple[KnowledgeExtractionWindow, ...]:
    """将 RAG 内容投影转换为知识抽取窗口。"""
    # 建立 chunk -> source refs 索引，方便后续绑定 evidence。
    source_refs_by_chunk: dict[str, list[RagSourceRef]] = {}
    for source_ref in source.source_refs:
        source_refs_by_chunk.setdefault(source_ref.chunk_id, []).append(source_ref)

    windows: list[KnowledgeExtractionWindow] = []

    chunks = source.chunks
    for index, chunk in enumerate(chunks):
        source_refs = tuple(source_refs_by_chunk.get(chunk.chunk_id, ()))

        # 无文本或无 source 定位信息的 chunk 无法提供可靠 evidence，跳过。
        if not chunk.raw_text.strip() or not source_refs:
            continue

        previous_chunk = chunks[index - 1] if index > 0 else None
        next_chunk = (
            chunks[index + 1] if index + 1 < len(chunks) else None
        )
        # 把每条 source span 重新映射到 chunk raw_text 的局部坐标，
        # 这样 LLM 给出的引文可以在窗口文本中定位后，再反向回算到原文 offset。
        source_mappings: list[KnowledgeWindowSourceSpan] = []
        search_start = 0
        for source_span in chunk.source_spans:
            source_text = source.markdown[source_span.start_offset: source_span.end_offset]
            local_start = chunk.raw_text.find(source_text, search_start)
            if not source_text or local_start < 0:
                raise ValueError(f"chunk {chunk.chunk_id} source span does not match raw text")
            local_end = local_start + len(source_text)
            source_mappings.append(
                KnowledgeWindowSourceSpan(
                    local_start=local_start,
                    local_end=local_end,
                    source_start=source_span.start_offset,
                    source_end=source_span.end_offset,
                )
            )
            search_start = local_end

        windows.append(
            KnowledgeExtractionWindow(
                resource_id=source.resource_id,
                document_version=source.document_version,
                content_revision=source.content_revision,
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.chunk_index,
                # 当前抽取目标文本。
                current_text=chunk.raw_text,
                # 保留原始定位信息，方便实体/关系 evidence 回溯。
                source_mappings=tuple(source_mappings),
                source_refs=source_refs,
                section_paths=(chunk.section_path,) if chunk.section_path else (),
                # 提供前后文辅助理解，例如跨 chunk 指代；仅使用同 section 邻居，不允许作为事实来源。
                previous_context=(
                    previous_chunk.raw_text[-_ADJACENT_CONTEXT_CHARS:]
                    if previous_chunk is not None
                       and previous_chunk.section_id == chunk.section_id
                    else ""
                ),
                next_context=(
                    next_chunk.raw_text[:_ADJACENT_CONTEXT_CHARS]
                    if next_chunk is not None
                       and next_chunk.section_id == chunk.section_id
                    else ""
                ),
            )
        )

    return tuple(windows)


def render_extraction_window(window: KnowledgeExtractionWindow) -> str:
    """将知识抽取窗口渲染为 LLM 输入。"""
    section_paths = "\n".join(" > ".join(path) for path in window.section_paths)

    return f"""EXTRACTION_RULES:
- Extract general entities and explicit cross-document relations.
- Extract only facts supported by CURRENT_CHUNK.
- evidence_quote must be one exact continuous substring of CURRENT_CHUNK.
- Context before and after CURRENT_CHUNK is only for disambiguation.
- Use only node types, entity types, relation types and endpoint directions from the schema.
- Use CURRENT_RESOURCE as the Resource node and copy its resource_id exactly.
- RELATED_TO requires a specific predicate.
- Return no node or relation when evidence is insufficient.

CURRENT_RESOURCE:
resource_id: {window.resource_id}
section_paths:
{section_paths}

PREVIOUS_CONTEXT:
{window.previous_context}

CURRENT_CHUNK:
{window.current_text}

NEXT_CONTEXT:
{window.next_context}
"""

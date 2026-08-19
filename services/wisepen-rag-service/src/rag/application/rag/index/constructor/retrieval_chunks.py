"""从 ReadingBlock 构建检索评分使用的 RetrievalChunk。

RetrievalChunk 是最细粒度的索引单元，主要用于向量检索：
- 每个 ReadingBlock 会被进一步切成多个 RetrievalChunk（默认 800 字符），
  使向量索引在更小范围内匹配，提升召回精度。
"""

from hashlib import sha256

from common.utils.document import (
    DocumentChunker,
    DocumentChunkerConfig,
    Section,
    SourceSpan,
)

from rag.domain.models.content import ReadingBlock
from rag.domain.models.retrieval import RetrievalChunk
from rag.domain.models.structure import DocumentStructure

from ._source_spans import _map_rendered_spans_to_source, _overlaps, _render_source_text

# 单个 RetrievalChunk 的最大字符数，与下游向量模型的最佳输入长度对齐。
_RETRIEVAL_CHUNK_MAX_CHARACTERS = 800
# 相邻 chunk 之间的字符重叠量，缓解超长 block 硬切割造成的语义损失。
_RETRIEVAL_CHUNK_OVERLAP = 100


def build_retrieval_chunks(
    *,
    markdown: str,
    structure: DocumentStructure,
    sections: list[Section],
    reading_blocks: list[ReadingBlock],
) -> list[RetrievalChunk]:
    """从上游刚构造的 ReadingBlock 生成用于评分的确定性子块。"""
    sections_by_id = {section.section_id: section for section in sections}

    # DocumentChunker 将 ReadingBlock 中解析出的 block 按字符预算装箱，
    # 由于保留了完整的markdown block结构，所以每个chunk本身语义没有被切断，
    # 只有超长block的递归切分过程中需要设置字符重叠，避免语义损失。
    chunker = DocumentChunker(
        DocumentChunkerConfig(
            max_characters=_RETRIEVAL_CHUNK_MAX_CHARACTERS,
            chunk_overlap=_RETRIEVAL_CHUNK_OVERLAP,
        )
    )
    chunks: list[RetrievalChunk] = []

    for reading_block in reading_blocks:
        # 父块可以覆盖多个兄弟 Section，但检索块和证据引用仍保持单 Section 归属。
        for section_id in reading_block.section_ids:
            section = sections_by_id[section_id]
            section_spans = [
                span
                for span in reading_block.source_spans
                if _overlaps(span, section.content_spans)
            ]
            if not section_spans:
                raise ValueError(
                    f"reading block {reading_block.block_id} has no content for "
                    f"section {section_id}"
                )
            result = chunker.chunk(_render_source_text(markdown, section_spans))
            for chunk in result.chunks:
                # chunk.source_spans 是当前 Section 渲染文本上的偏移，
                # 需要映射回原文坐标，使其可以独立回源。
                source_spans = _map_rendered_spans_to_source(
                    rendered_spans=list(chunk.source_spans),
                    source_spans=section_spans,
                )
                # raw_text 必须直接从原文渲染，确保与权威源完全一致。
                raw_text = _render_source_text(markdown, source_spans)
                chunks.append(
                    RetrievalChunk(
                        chunk_id=_build_retrieval_chunk_id(
                            reading_block_id=reading_block.block_id,
                            source_spans=source_spans,
                        ),
                        reading_block_id=reading_block.block_id,
                        section_id=section.section_id,
                        section_path=list(section.section_path),
                        raw_text=raw_text,
                        index_text=raw_text,
                        source_spans=source_spans,
                        page_labels=[
                            page.page_label
                            for page in structure.pages
                            if _overlaps(page.source_span, source_spans)
                        ],
                        anchor_labels=[
                            anchor.label
                            for anchor in structure.anchors
                            if _overlaps(anchor.source_span, source_spans)
                        ],
                    )
                )

    return chunks


def _build_retrieval_chunk_id(
    *,
    reading_block_id: str,
    source_spans: list[SourceSpan],
) -> str:
    """基于 ReadingBlock 和 span 边界生成稳定的 RetrievalChunk ID。"""
    span_identity = ";".join(
        f"{span.start_offset}:{span.end_offset}" for span in source_spans
    )
    identity = f"{reading_block_id}\0{span_identity}"
    return f"rrc_{sha256(identity.encode('utf-8')).hexdigest()[:16]}"

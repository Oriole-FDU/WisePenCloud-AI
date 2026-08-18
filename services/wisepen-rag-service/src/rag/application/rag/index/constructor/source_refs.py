"""构建 RetrievalChunk 到权威原文的 SourceRef。

SourceRef 是检索结果到原文的回源凭据。检索命中后可通过 SourceRef
直接定位到原文位置、所属章节、所属页面与锚点，无需回查 chunk 本身。
"""

from hashlib import sha256

from rag.domain.models.provenance import SourceRef
from rag.domain.models.retrieval import RetrievalChunk


def build_source_refs(
    *,
    resource_id: str,
    content_revision: str,
    retrieval_chunks: list[RetrievalChunk],
) -> list[SourceRef]:
    """把上游刚构造的 RetrievalChunk 固化为完整归属链。

    chunk 的全部归属字段（block、section、path、spans、labels）在上游构造时已确定，
    这里仅负责组装与派生 ref_id。
    """
    return [
        SourceRef(
            ref_id=_build_source_ref_id(
                resource_id=resource_id,
                content_revision=content_revision,
                chunk=chunk,
            ),
            resource_id=resource_id,
            content_revision=content_revision,
            chunk_id=chunk.chunk_id,
            reading_block_id=chunk.reading_block_id,
            section_id=chunk.section_id,
            section_path=chunk.section_path,
            source_spans=chunk.source_spans,
            page_labels=chunk.page_labels,
            anchor_labels=chunk.anchor_labels,
        )
        for chunk in retrieval_chunks
    ]


def _build_source_ref_id(
    *,
    resource_id: str,
    content_revision: str,
    chunk: RetrievalChunk,
) -> str:
    """基于资源、revision、chunk、block、section 五元组生成稳定 SourceRef ID。"""
    identity = (
        f"{resource_id}\0{content_revision}\0{chunk.chunk_id}"
        f"\0{chunk.reading_block_id}\0{chunk.section_id}"
    )
    return f"rsrc_{sha256(identity.encode('utf-8')).hexdigest()[:16]}"

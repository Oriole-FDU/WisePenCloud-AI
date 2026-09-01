"""从上游 Markdown 生成 staged 的 Document 与 DocChunk 事实。"""

from dataclasses import replace

from common.utils.document import (
    DocumentChunk,
    DocumentChunker,
    DocumentChunkerConfig,
    Section,
)

from rag_v3.application.publication import DocumentPublication
from rag_v3.domain.models import (
    ContentRevision,
    DocChunk,
    Document,
    DocumentMetadata,
    DocumentStructure,
    GeneralDocumentMetadata,
    rag_chunk_id,
    rag_section_id,
)
from rag_v3.domain.repositories.doc_chunks import DocChunkRepository
from rag_v3.domain.repositories.index_state import StageAction


class DocumentPreparer:
    """将一次 Common 分块结果投影为 RAG 的 staged 内容事实。"""

    def __init__(
        self,
        *,
        publication: DocumentPublication,
        doc_chunks: DocChunkRepository,
    ) -> None:
        self._publication = publication
        self._doc_chunks = doc_chunks

    async def prepare(
        self,
        *,
        resource_id: str,
        document_version: int,
        markdown: str,
        metadata: DocumentMetadata | None = None,
    ) -> StageAction:
        """构造并暂存一版文档事实，绝不在此阶段发布 active 指针。"""
        revision = ContentRevision.create(
            resource_id=resource_id,
            document_version=document_version,
            raw_content=markdown,
        )
        chunking = DocumentChunker(
            DocumentChunkerConfig(max_characters=800, chunk_overlap=100)
        ).chunk(markdown)

        section_ids = {
            section.section_id: rag_section_id(
                resource_id=resource_id,
                content_revision=revision.content_revision,
                common_section_id=section.section_id,
            )
            for section in chunking.sections
        }
        sections = tuple(
            replace(
                section,
                section_id=section_ids[section.section_id],
                parent_section_id=(
                    None
                    if section.parent_section_id is None
                    else section_ids[section.parent_section_id]
                ),
            )
            for section in chunking.sections
        )
        sections_by_id = {section.section_id: section for section in sections}
        document = Document(
            resource_id=resource_id,
            revision=revision,
            raw_content=markdown,
            structure=DocumentStructure(
                total_length=len(markdown),
                sections=sections,
                pages=chunking.pages,
                anchors=chunking.anchors,
            ),
            metadata=metadata or GeneralDocumentMetadata(),
        )
        chunks = tuple(
            _to_doc_chunk(
                chunk,
                resource_id=resource_id,
                content_revision=revision.content_revision,
                section_ids=section_ids,
                sections_by_id=sections_by_id,
                raw_content=markdown,
            )
            for chunk in chunking.chunks
        )

        action = await self._publication.stage_document(document)
        if action is StageAction.STALE:
            return action
        await self._doc_chunks.save_revision(chunks)
        return action


def _to_doc_chunk(
    chunk: DocumentChunk,
    *,
    resource_id: str,
    content_revision: str,
    section_ids: dict[str, str],
    sections_by_id: dict[str, Section],
    raw_content: str,
) -> DocChunk:
    for span in chunk.source_spans:
        if span.end_offset > len(raw_content):
            raise ValueError("Common chunk span is outside markdown")

    section_id = None if chunk.section_id is None else section_ids[chunk.section_id]
    section_path = () if section_id is None else sections_by_id[section_id].section_path
    return DocChunk(
        chunk_id=rag_chunk_id(
            resource_id=resource_id,
            content_revision=content_revision,
            common_chunk_id=chunk.chunk_id,
        ),
        resource_id=resource_id,
        content_revision=content_revision,
        chunk_index=chunk.chunk_index,
        section_id=section_id,
        section_path=section_path,
        raw_text=chunk.text,
        source_spans=chunk.source_spans,
        page_labels=chunk.page_labels,
        anchor_labels=chunk.anchor_labels,
    )

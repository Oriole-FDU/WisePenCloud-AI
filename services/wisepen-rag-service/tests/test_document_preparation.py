"""P1-A 确定性文档准备：Common 事实投影、暂存与 DocChunk。"""

import pytest
from common.utils.document import DocumentChunker, DocumentChunkerConfig, SourceSpan

from rag.application.document.models import (
    ContentRevision,
    DocChunk,
    rag_chunk_id,
    rag_section_id,
)
from rag.application.document.preparation import DocumentPreparer
from rag.application.publication import DocumentPublication
from rag.application.snapshot import ActiveDocumentSnapshotLoader
from rag.domain.acl import PermissionScope, ResourceAcl
from rag.domain.repositories.index_state import StageAction

from .conftest import (
    MemoryAcls,
    MemoryDocChunks,
    MemoryDocuments,
    MemoryDocumentVectors,
    MemoryIndexStates,
)


def _readable_acl(resource_id: str) -> ResourceAcl:
    return ResourceAcl(
        resource_id=resource_id,
        acl_revision=1,
        owner_id="owner",
        readable_users=("reader",),
    )


def _preparer() -> tuple[
    DocumentPreparer, MemoryDocuments, MemoryIndexStates, MemoryDocChunks
]:
    documents = MemoryDocuments()
    states = MemoryIndexStates()
    chunks = MemoryDocChunks()
    vectors = MemoryDocumentVectors()
    return (
        DocumentPreparer(
            publication=DocumentPublication(
                documents=documents,
                doc_chunks=chunks,
                document_vectors=vectors,
                index_states=states,
            ),
            doc_chunks=chunks,
        ),
        documents,
        states,
        chunks,
    )


@pytest.mark.asyncio
async def test_prepare_projects_common_result_with_global_ids_and_stays_staged() -> (
    None
):
    markdown = """前言正文。\n\n<!-- page 1 -->\n\n# 数据\n\n正文。\n\nTable 1: 样例\n\n| 值 |\n| --- |\n| 甲 |\n"""
    common_result = DocumentChunker(
        DocumentChunkerConfig(max_characters=800, chunk_overlap=100)
    ).chunk(markdown)
    preparer, documents, states, chunks = _preparer()

    assert (
        await preparer.prepare(
            resource_id="resource", document_version=1, markdown=markdown
        )
        is StageAction.STAGED
    )

    revision = ContentRevision.create(
        resource_id="resource", document_version=1, raw_content=markdown
    )
    document = documents.documents[("resource", revision.content_revision)]
    saved_chunks = await chunks.get_revision_chunks(
        resource_id="resource", content_revision=revision.content_revision
    )

    common_to_global = {
        section.section_id: rag_section_id(
            resource_id="resource",
            content_revision=revision.content_revision,
            common_section_id=section.section_id,
        )
        for section in common_result.sections
    }
    assert [section.section_id for section in document.structure.sections] == list(
        common_to_global.values()
    )
    assert all(
        section.parent_section_id is None
        or section.parent_section_id in common_to_global.values()
        for section in document.structure.sections
    )
    assert [chunk.raw_text for chunk in saved_chunks] == [
        chunk.text for chunk in common_result.chunks
    ]
    assert [chunk.source_spans for chunk in saved_chunks] == [
        chunk.source_spans for chunk in common_result.chunks
    ]
    assert [chunk.page_labels for chunk in saved_chunks] == [
        chunk.page_labels for chunk in common_result.chunks
    ]
    assert [chunk.anchor_labels for chunk in saved_chunks] == [
        chunk.anchor_labels for chunk in common_result.chunks
    ]
    assert [chunk.section_id for chunk in saved_chunks] == [
        None if chunk.section_id is None else common_to_global[chunk.section_id]
        for chunk in common_result.chunks
    ]
    assert all(chunk.chunk_id.startswith("rchk_") for chunk in saved_chunks)
    assert states.states["resource"].applied_content_revision is None

    snapshots = ActiveDocumentSnapshotLoader(
        documents=documents,
        index_states=states,
        resource_acls=MemoryAcls({"resource": _readable_acl("resource")}),
    )
    assert (
        await snapshots.load_documents(["resource"], scope=PermissionScope("reader"))
        == {}
    )


@pytest.mark.asyncio
async def test_prepare_is_idempotent_and_revision_scoped() -> None:
    preparer, _, states, chunks = _preparer()
    markdown = "# 标题\n\n正文。\n"

    assert (
        await preparer.prepare(
            resource_id="resource", document_version=1, markdown=markdown
        )
        is StageAction.STAGED
    )
    first_revision = states.states["resource"].staged_content_revision
    first_chunks = await chunks.get_revision_chunks(
        resource_id="resource", content_revision=first_revision
    )
    assert (
        await preparer.prepare(
            resource_id="resource", document_version=1, markdown=markdown
        )
        is StageAction.STAGED
    )
    assert [chunk.chunk_id for chunk in first_chunks] == [
        chunk.chunk_id
        for chunk in await chunks.get_revision_chunks(
            resource_id="resource", content_revision=first_revision
        )
    ]

    assert (
        await preparer.prepare(
            resource_id="resource", document_version=2, markdown=markdown
        )
        is StageAction.STAGED
    )
    second_revision = states.states["resource"].staged_content_revision
    second_chunks = await chunks.get_revision_chunks(
        resource_id="resource", content_revision=second_revision
    )
    assert second_revision != first_revision
    assert {chunk.chunk_id for chunk in first_chunks}.isdisjoint(
        chunk.chunk_id for chunk in second_chunks
    )


@pytest.mark.asyncio
async def test_old_version_does_not_write_chunks_after_newer_revision_is_active() -> (
    None
):
    preparer, documents, states, chunks = _preparer()
    markdown = "# 标题\n\n正文。\n"
    assert (
        await preparer.prepare(
            resource_id="resource", document_version=2, markdown=markdown
        )
        is StageAction.STAGED
    )
    revision = ContentRevision.create(
        resource_id="resource", document_version=2, raw_content=markdown
    )
    vectors = MemoryDocumentVectors()
    vectors.revision_chunk_ids[("resource", revision.content_revision)] = {
        chunk.chunk_id
        for chunk in await chunks.get_revision_chunks(
            resource_id="resource",
            content_revision=revision.content_revision,
        )
    }
    await DocumentPublication(
        documents=documents,
        doc_chunks=chunks,
        document_vectors=vectors,
        index_states=states,
    ).apply_revision(revision)
    save_calls = chunks.save_calls

    assert (
        await preparer.prepare(
            resource_id="resource", document_version=1, markdown=markdown
        )
        is StageAction.STALE
    )
    assert chunks.save_calls == save_calls


@pytest.mark.asyncio
async def test_flat_and_oversized_markdown_keep_common_chunk_text_and_spans() -> None:
    markdown = ("无标题正文。" * 300) + "\n"
    common_result = DocumentChunker(
        DocumentChunkerConfig(max_characters=800, chunk_overlap=100)
    ).chunk(markdown)
    preparer, _, states, chunks = _preparer()

    await preparer.prepare(resource_id="flat", document_version=1, markdown=markdown)
    saved_chunks = await chunks.get_revision_chunks(
        resource_id="flat",
        content_revision=states.states["flat"].staged_content_revision,
    )

    assert len(saved_chunks) > 1
    assert all(
        chunk.section_id is None and chunk.section_path == [] for chunk in saved_chunks
    )
    assert [(chunk.raw_text, chunk.source_spans) for chunk in saved_chunks] == [
        (chunk.text, chunk.source_spans) for chunk in common_result.chunks
    ]
    assert all(
        0 <= span.start_offset <= span.end_offset <= len(markdown)
        for chunk in saved_chunks
        for span in chunk.source_spans
    )


def test_doc_chunk_index_text_is_rebuilt_from_business_fields() -> None:
    chunk = ("common-chunk", "resource", "resource@1#hash", "正文")
    doc_chunk = DocChunk(
        chunk_id=rag_chunk_id(
            resource_id=chunk[1], content_revision=chunk[2], common_chunk_id=chunk[0]
        ),
        resource_id=chunk[1],
        content_revision=chunk[2],
        chunk_index=0,
        section_id="rsec",
        section_path=("标题", "小节"),
        raw_text=chunk[3],
        source_spans=(SourceSpan(0, 2),),
        contextual_prefix="上下文",
        key_terms=("术语A", "术语B"),
    )

    assert doc_chunk.get_semantic_text() == "标题 > 小节\n\n上下文\n\n正文"
    assert doc_chunk.get_lexical_text() == "标题 小节 术语A 术语B 正文"

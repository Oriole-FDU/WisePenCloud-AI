"""revision 发布和 active 文档快照的 P0 行为。"""

import pytest

from rag_v3.application.document.models import rag_section_id
from rag_v3.application.publication import DocumentPublication
from rag_v3.application.snapshot import ActiveDocumentSnapshotLoader
from rag_v3.domain.acl import PermissionScope, ResourceAcl
from rag_v3.domain.repositories.index_state import StageAction

from .conftest import (
    MemoryAcls,
    MemoryDocChunks,
    MemoryDocuments,
    MemoryDocumentVectors,
    MemoryIndexStates,
    chunk_for_document,
    document,
)


def readable_acl(resource_id: str) -> ResourceAcl:
    return ResourceAcl(
        resource_id=resource_id,
        acl_revision=1,
        owner_id="owner",
        readable_users=("reader",),
    )


@pytest.mark.asyncio
async def test_staged_revision_is_invisible_until_apply_then_replaced() -> None:
    documents = MemoryDocuments()
    states = MemoryIndexStates()
    chunks = MemoryDocChunks()
    vectors = MemoryDocumentVectors()
    publication = DocumentPublication(
        documents=documents,
        doc_chunks=chunks,
        document_vectors=vectors,
        index_states=states,
    )
    snapshots = ActiveDocumentSnapshotLoader(
        documents=documents,
        index_states=states,
        resource_acls=MemoryAcls({"resource": readable_acl("resource")}),
    )
    first = document(resource_id="resource", version=1, section_id="rsec-v1")
    second = document(resource_id="resource", version=2, section_id="rsec-v2")
    for item in (first, second):
        chunk = chunk_for_document(item)
        await chunks.save_revision([chunk])
        vectors.revision_chunk_ids[(item.resource_id, item.revision.content_revision)] = {
            chunk.chunk_id
        }
    scope = PermissionScope("reader")

    assert await publication.stage_document(first) is StageAction.STAGED
    assert await snapshots.load_documents(["resource"], scope=scope) == {}
    await publication.apply_revision(first.revision)
    assert (await snapshots.load_documents(["resource"], scope=scope))["resource"] == first

    assert await publication.stage_document(second) is StageAction.STAGED
    assert (await snapshots.load_documents(["resource"], scope=scope))["resource"] == first
    await publication.apply_revision(second.revision)
    assert (await snapshots.load_documents(["resource"], scope=scope))["resource"] == second
    assert await publication.stage_document(first) is StageAction.STALE


@pytest.mark.asyncio
async def test_apply_requires_persisted_document() -> None:
    documents = MemoryDocuments()
    states = MemoryIndexStates()
    chunks = MemoryDocChunks()
    publication = DocumentPublication(
        documents=documents,
        doc_chunks=chunks,
        document_vectors=MemoryDocumentVectors(),
        index_states=states,
    )
    pending = document(resource_id="resource", version=1, section_id="rsec")
    await states.stage_revision(
        pending.revision,
        expected_applied_content_revision=None,
    )

    with pytest.raises(ValueError, match="not persisted"):
        await publication.apply_revision(pending.revision)


@pytest.mark.asyncio
async def test_section_locator_filters_old_revision_and_missing_acl_in_batches() -> None:
    documents = MemoryDocuments()
    states = MemoryIndexStates()
    chunks = MemoryDocChunks()
    vectors = MemoryDocumentVectors()
    publication = DocumentPublication(
        documents=documents,
        doc_chunks=chunks,
        document_vectors=vectors,
        index_states=states,
    )
    first = document(resource_id="one", version=1, section_id="rsec-one-v1")
    second = document(resource_id="one", version=2, section_id="rsec-one-v2")
    unreadable = document(resource_id="two", version=1, section_id="rsec-two-v1")
    for item in (first, second, unreadable):
        chunk = chunk_for_document(item)
        await chunks.save_revision([chunk])
        vectors.revision_chunk_ids[(item.resource_id, item.revision.content_revision)] = {
            chunk.chunk_id
        }
        await publication.stage_document(item)
        await publication.apply_revision(item.revision)

    acls = MemoryAcls({"one": readable_acl("one")})
    snapshots = ActiveDocumentSnapshotLoader(
        documents=documents,
        index_states=states,
        resource_acls=acls,
    )
    states.get_states_calls = 0
    locations = await snapshots.locate_sections(
        ["rsec-one-v1", "rsec-one-v2", "rsec-two-v1", "missing"],
        scope=PermissionScope("reader"),
    )

    assert list(locations) == ["rsec-one-v2"]
    assert locations["rsec-one-v2"].document == second
    assert documents.section_lookup_calls == 1
    assert states.get_states_calls == 1
    assert acls.get_acls_calls == 1


def test_global_section_id_is_resource_and_revision_scoped() -> None:
    first = rag_section_id(
        resource_id="resource",
        content_revision="resource@1#hash",
        common_section_id="sec_local",
    )
    assert first == rag_section_id(
        resource_id="resource",
        content_revision="resource@1#hash",
        common_section_id="sec_local",
    )
    assert first != rag_section_id(
        resource_id="resource",
        content_revision="resource@2#hash",
        common_section_id="sec_local",
    )

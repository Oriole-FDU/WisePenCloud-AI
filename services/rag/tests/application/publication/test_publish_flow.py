"""固定普通摄入顺序与失败边界，不通过运行时装配检查证明内部契约。"""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rag.application.publication import (
    ContentConflictError,
    ContentIdentityError,
    DocumentPublication,
    PublicationAction,
)
from rag.core.schema import ContentRevision
from rag.ingestion.chunkers import MarkdownChunker
from rag.ingestion.indexers import ChunkFactsIndexer
from rag.ingestion.pipeline import IngestionPipeline


async def test_publish_saves_facts_before_every_required_projection(
    storage, publication_store, make_document
):
    calls = []
    document = make_document()
    revision = ContentRevision.create(
        resource_id=document.resource_id,
        document_version=document.document_version,
        text=document.text,
    )
    original_stage = publication_store.stage_revision
    original_write = storage.upsert_documents
    original_chunks = storage.upsert_chunks
    original_documents_read = storage.get_documents
    original_chunks_read = storage.get_revision_chunks
    original_apply = publication_store.apply_revision

    async def stage(current):
        calls.append("stage")
        return await original_stage(current)

    async def write(documents):
        assert documents[0] is document
        assert (await publication_store.get_states(["resource"]))[
            0
        ].staged_revision == revision
        calls.append("document")
        await original_write(documents)

    async def transform(chunks, *, document=None):
        calls.append("transform")
        chunks[0].metadata["contextual_context"] = "增强上下文"
        return chunks

    async def write_chunks(chunks):
        calls.append("chunks")
        await original_chunks(chunks)

    async def project(chunks):
        calls.append("projection-1")
        saved = await original_chunks_read(
            resource_id=document.resource_id, content_revision=document.content_revision
        )
        assert saved == chunks
        assert saved[0].retrieval_text.startswith("增强上下文\n\n")
        assert (await publication_store.get_states(["resource"]))[
            0
        ].applied_revision is None

    async def second_projection(chunks):
        calls.append("projection-2")

    async def read_documents(keys):
        calls.append("read-document")
        return await original_documents_read(keys)

    async def read_chunks(**kwargs):
        calls.append("read-chunks")
        return await original_chunks_read(**kwargs)

    async def apply(current):
        calls.append("apply")
        return await original_apply(current)

    publication_store.stage_revision = stage
    storage.upsert_documents = write
    storage.upsert_chunks = write_chunks
    storage.get_documents = read_documents
    storage.get_revision_chunks = read_chunks
    publication_store.apply_revision = apply
    first = SimpleNamespace(index=AsyncMock(side_effect=project))
    second = SimpleNamespace(index=AsyncMock(side_effect=second_projection))
    publication = DocumentPublication(
        store=publication_store,
        reader=storage,
        writer=storage,
        pipeline=IngestionPipeline(
            chunker=MarkdownChunker(),
            transformers=[SimpleNamespace(transform=AsyncMock(side_effect=transform))],
            indexers=[ChunkFactsIndexer(writer=storage), first, second],
        ),
    )
    assert await publication.publish(document) is PublicationAction.APPLIED
    assert calls == [
        "stage",
        "document",
        "transform",
        "chunks",
        "projection-1",
        "projection-2",
        "read-document",
        "read-chunks",
        "apply",
    ]
    assert first.index.call_args.args[0] is second.index.call_args.args[0]
    state = (await publication_store.get_states(["resource"]))[0]
    assert state.applied_revision == revision and state.staged_revision is None


async def test_identity_mismatch_has_no_io(
    publication, collections, publication_collection, projection, make_document
):
    with pytest.raises(ContentIdentityError):
        await publication.publish(replace(make_document(), content_revision="wrong"))
    for collection in collections:
        collection.bulk_write.assert_not_awaited()
    publication_collection.find_one.assert_not_awaited()
    publication_collection.insert_one.assert_not_awaited()
    projection.index.assert_not_awaited()


@pytest.mark.parametrize("skip", ["duplicate", "stale", "destroyed"])
async def test_ignored_submissions_do_not_write_or_rebuild_metadata(
    publication,
    publication_store,
    collections,
    projection,
    storage,
    make_document,
    skip,
):
    document = make_document()
    assert await publication.publish(document) is PublicationAction.APPLIED
    if skip == "stale":
        await publication_store.stage_revision(
            ContentRevision.create(
                resource_id="resource", document_version=2, text="新版"
            )
        )
    elif skip == "destroyed":
        await publication_store.destroy_resources(["resource"])
    before = [list(collection.records) for collection in collections]
    for collection in collections:
        collection.bulk_write.reset_mock()
    projection.index.reset_mock()
    submission = replace(document, metadata={"changed": True})
    expected = {
        "duplicate": PublicationAction.ALREADY_APPLIED,
        "stale": PublicationAction.STALE,
        "destroyed": PublicationAction.DESTROYED,
    }[skip]
    assert await publication.publish(submission) is expected
    assert [collection.records for collection in collections] == before
    for collection in collections:
        collection.bulk_write.assert_not_awaited()
    projection.index.assert_not_awaited()
    assert (await storage.get_documents([("resource", document.content_revision)]))[
        0
    ].metadata == document.metadata


async def test_same_version_conflict_stops_before_content_or_projection(
    publication, collections, projection, make_document
):
    await publication.publish(make_document())
    for collection in collections:
        collection.bulk_write.reset_mock()
    projection.index.reset_mock()
    with pytest.raises(ContentConflictError):
        await publication.publish(make_document(text="冲突正文"))
    for collection in collections:
        collection.bulk_write.assert_not_awaited()
    projection.index.assert_not_awaited()


@pytest.mark.parametrize(
    "failed_stage", ["document", "transformer", "chunks", "projection"]
)
async def test_failed_build_preserves_old_applied_and_completed_facts(
    storage, publication_store, make_document, failed_stage
):
    projection = SimpleNamespace(index=AsyncMock())
    later_projection = SimpleNamespace(index=AsyncMock())
    transformer = SimpleNamespace(
        transform=AsyncMock(side_effect=lambda chunks, **kwargs: chunks)
    )
    publication = DocumentPublication(
        store=publication_store,
        reader=storage,
        writer=storage,
        pipeline=IngestionPipeline(
            chunker=MarkdownChunker(),
            transformers=[transformer],
            indexers=[ChunkFactsIndexer(writer=storage), projection, later_projection],
        ),
    )
    first, candidate = make_document(), make_document(version=2)
    await publication.publish(first)
    error = RuntimeError("build failed")
    projection.index.reset_mock()
    later_projection.index.reset_mock()
    publication_store.apply_revision = AsyncMock(wraps=publication_store.apply_revision)
    if failed_stage == "document":
        storage.upsert_documents = AsyncMock(side_effect=error)
    elif failed_stage == "chunks":
        storage.upsert_chunks = AsyncMock(side_effect=error)
    elif failed_stage == "transformer":
        transformer.transform.side_effect = error
    else:
        projection.index.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        await publication.publish(candidate)
    assert caught.value is error
    publication_store.apply_revision.assert_not_awaited()
    later_projection.index.assert_not_awaited()
    if failed_stage != "projection":
        projection.index.assert_not_awaited()
    state = (await publication_store.get_states(["resource"]))[0]
    assert state.applied_revision.content_revision == first.content_revision
    assert state.staged_revision.content_revision == candidate.content_revision
    documents = await storage.get_documents([("resource", candidate.content_revision)])
    chunks = await storage.get_revision_chunks(
        resource_id="resource", content_revision=candidate.content_revision
    )
    assert bool(documents) is (failed_stage != "document")
    assert bool(chunks) is (failed_stage == "projection")


async def test_staged_replay_can_finish_after_projection_failure(
    publication, projection, publication_store, make_document
):
    document = make_document()
    projection.index.side_effect = RuntimeError("projection failed")
    with pytest.raises(RuntimeError):
        await publication.publish(document)
    state = (await publication_store.get_states(["resource"]))[0]
    assert state.staged_revision.content_revision == document.content_revision
    assert state.applied_revision is None
    projection.index.side_effect = None
    assert await publication.publish(document) is PublicationAction.APPLIED


@pytest.mark.parametrize("text", ["", " \n\t\n"])
async def test_zero_chunk_documents_are_published(
    publication, projection, storage, publication_store, make_document, text
):
    document = make_document(text=text)
    assert await publication.publish(document) is PublicationAction.APPLIED
    projection.index.assert_awaited_once_with([])
    assert (
        await storage.get_revision_chunks(
            resource_id="resource", content_revision=document.content_revision
        )
        == []
    )
    assert await storage.get_documents([("resource", document.content_revision)]) == [
        document
    ]
    assert (await publication_store.get_states(["resource"]))[
        0
    ].applied_revision.content_revision == document.content_revision


async def test_heading_only_document_is_published_with_common_chunking(
    publication, projection, storage, make_document
):
    document = make_document(text="# 只有标题\n")
    assert await publication.publish(document) is PublicationAction.APPLIED
    chunks = await storage.get_revision_chunks(
        resource_id="resource", content_revision=document.content_revision
    )
    # Common 当前将仅标题原文作为一个 flat Chunk；发布不强加“必须零 Chunk”。
    assert len(chunks) == 1 and chunks[0].raw_text == document.text
    projection.index.assert_awaited_once_with(chunks)


@pytest.mark.parametrize("change", ["newer", "destroyed"])
@pytest.mark.parametrize("timing", ["projection", "read"])
async def test_build_or_read_race_cannot_publish_invalidated_candidate(
    publication, projection, publication_store, storage, make_document, change, timing
):
    first, candidate = make_document(), make_document(version=2)
    await publication.publish(first)
    newer = ContentRevision.create(
        resource_id="resource", document_version=3, text="更高版本"
    )

    async def invalidate():
        if change == "newer":
            await publication_store.stage_revision(newer)
        else:
            await publication_store.destroy_resources(["resource"])

    if timing == "projection":

        async def project(chunks):
            await invalidate()

        projection.index.side_effect = project
    else:
        original_read = storage.get_revision_chunks

        async def read(**kwargs):
            chunks = await original_read(**kwargs)
            await invalidate()
            return chunks

        storage.get_revision_chunks = read
    expected = (
        PublicationAction.STALE if change == "newer" else PublicationAction.DESTROYED
    )
    assert await publication.publish(candidate) is expected
    state = (await publication_store.get_states(["resource"]))[0]
    if change == "newer":
        assert state.staged_revision == newer
        assert state.applied_revision.content_revision == first.content_revision
    else:
        assert (
            state.destroyed
            and state.applied_revision is None
            and state.staged_revision is None
        )
    # 失效构建仍可留下事实；本轮不把发布失效扩展为物理补偿流程。
    assert await storage.get_documents([("resource", candidate.content_revision)]) == [
        candidate
    ]

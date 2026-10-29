"""从完整 publish 入口验证事实回查，错误事实不能改变发布可见性。"""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rag.application.publication import ContentIncompleteError, DocumentPublication
from rag.domain.models.publication import PublicationAction
from rag.domain.models import ContentRevision


@pytest.mark.parametrize(
    "corruption", ["missing", "resource", "version", "text", "revision"]
)
async def test_document_identity_mismatch_blocks_apply(make_document, corruption):
    document = make_document()
    corrupted = document
    if corruption == "resource":
        corrupted = replace(document, resource_id="other")
    elif corruption == "version":
        corrupted = replace(document, document_version=2)
    elif corruption == "text":
        corrupted = replace(document, text=document.text + "\n")
    elif corruption == "revision":
        corrupted = replace(document, content_revision="wrong")
    reader = SimpleNamespace(
        get_documents=AsyncMock(
            return_value=[] if corruption == "missing" else [corrupted]
        ),
        get_revision_chunks=AsyncMock(),
    )
    store = SimpleNamespace(
        stage_revision=AsyncMock(return_value=PublicationAction.STAGED),
        apply_revision=AsyncMock(),
    )
    writer = SimpleNamespace(upsert_documents=AsyncMock())
    pipeline = SimpleNamespace(run=AsyncMock(return_value=[]))
    publication = DocumentPublication(
        store=store, reader=reader, writer=writer, pipeline=pipeline
    )
    with pytest.raises(ContentIncompleteError):
        await publication.publish(document)
    writer.upsert_documents.assert_awaited_once_with([document])
    pipeline.run.assert_awaited_once_with(document)
    reader.get_revision_chunks.assert_not_awaited()
    store.apply_revision.assert_not_awaited()


@pytest.mark.parametrize("collection", ["missing", "extra", "reordered", "complete"])
async def test_expected_chunk_collection_is_exact_and_revision_scoped(
        publication_store, storage, make_document, chunk, collection
):
    document = make_document()
    revision = ContentRevision.create(
        resource_id=document.resource_id,
        document_version=document.document_version,
        text=document.text,
    )
    first = replace(
        chunk,
        chunk_id="first",
        content_revision=revision.content_revision,
        chunk_index=0,
    )
    second = replace(first, chunk_id="second", chunk_index=1)
    stored = [first, second]
    if collection == "missing":
        stored = [first]
    elif collection == "extra":
        stored.append(replace(first, chunk_id="extra", chunk_index=2))
    elif collection == "reordered":
        stored = [replace(first, chunk_index=1), replace(second, chunk_index=0)]
    # 别的资源和 revision 的事实不影响本 revision 的完整性。
    stored.extend(
        [
            replace(first, chunk_id="other-resource", resource_id="other"),
            replace(first, chunk_id="other-revision", content_revision="other"),
        ]
    )

    async def build(current):
        await storage.upsert_chunks(stored)
        # 模拟投影成功后返回本次预期集合，回查必须拒绝实际存储的缺失或多余事实。
        return [first, second]

    publication = DocumentPublication(
        store=publication_store,
        reader=storage,
        writer=storage,
        pipeline=SimpleNamespace(run=AsyncMock(side_effect=build)),
    )
    if collection == "complete":
        assert await publication.publish(document) is PublicationAction.APPLIED
    else:
        with pytest.raises(ContentIncompleteError):
            await publication.publish(document)
        assert (await publication_store.get_states(["resource"]))[
                   0
               ].applied_revision is None


@pytest.mark.parametrize("failed_read", ["get_documents", "get_revision_chunks"])
async def test_content_read_failures_propagate_without_apply(
        publication, publication_store, storage, make_document, failed_read
):
    error = RuntimeError("read failed")
    setattr(storage, failed_read, AsyncMock(side_effect=error))
    publication_store.apply_revision = AsyncMock(wraps=publication_store.apply_revision)
    with pytest.raises(RuntimeError) as caught:
        await publication.publish(make_document())
    assert caught.value is error
    publication_store.apply_revision.assert_not_awaited()

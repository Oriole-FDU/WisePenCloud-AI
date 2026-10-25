"""显式测试连接下验证真实 Mongo 发布状态、唯一索引与条件竞争。"""

import asyncio
import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import TypeAdapter
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

from rag.application.gc import GcReport, ResourceGarbageCollector
from rag.application.publication import (
    DocumentPublication,
    PublicationAction,
    PublicationStateChangedError,
    ResourcePublicationState,
)
from rag.backends.mongo import MongoContentStorage, MongoPublicationStore
from rag.backends.mongo.acl import MongoResourceAclRepository
from rag.core.schema import ContentRevision
from rag.ingestion.chunkers import MarkdownChunker
from rag.ingestion.indexers import ChunkFactsIndexer
from rag.ingestion.pipeline import IngestionPipeline

pytestmark = pytest.mark.mongo_integration


@pytest_asyncio.fixture
async def mongo_publication():
    uri = os.environ.get("RAG_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("未配置 RAG_TEST_MONGODB_URI，跳过真实 Mongo 集成测试")
    client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000)
    database_name = f"codex_rag_publication_test_{uuid4().hex}"
    database = client[database_name]
    store = MongoPublicationStore(database["resource_publications"])
    try:
        await store.ensure_indexes()
        yield store, database
    finally:
        try:
            # 只清理当前 fixture 创建的随机库，销毁标记不会触及其他数据库。
            await client.drop_database(database_name)
        finally:
            await client.close()


def revision(version=1, resource_id="resource"):
    return ContentRevision.create(
        resource_id=resource_id, document_version=version, text="  中文😀\n"
    )


async def test_real_gc_keyset_all_revisions_local_acl_and_permanent_markers(
        mongo_publication,
):
    store, database = mongo_publication
    storage = MongoContentStorage(
        documents=database["document_revisions"], chunks=database["doc_chunks"]
    )
    acls = MongoResourceAclRepository(database["resource_acls"])
    await storage.ensure_indexes()
    await acls.ensure_indexes()
    for resource in ("c", "a", "b", "live"):
        for version in (1, 2):
            from rag.application.publication import DocumentReadyEvent

            doc = DocumentReadyEvent.model_validate(
                {"resourceId": resource, "version": version, "content": "正文"}
            ).to_document()
            await storage.upsert_documents([doc])
            await storage.upsert_chunks(await MarkdownChunker().split(doc))
        await database["resource_acls"].insert_one({"resource_id": resource})
    await store.destroy_resources(["c", "a", "b"])
    gc = ResourceGarbageCollector(
        store=store,
        content_writer=storage,
        resource_acls=acls,
        vector_writers=[],
        graph_writers=[],
        batch_size=2,
    )
    assert await gc.collect() == GcReport(3, 3, 0)
    assert await database["document_revisions"].distinct("resource_id") == ["live"]
    assert await database["doc_chunks"].distinct("resource_id") == ["live"]
    assert await database["resource_acls"].distinct("resource_id") == ["live"]
    assert await store.list_destroyed_resource_ids() == ["a", "b", "c"]
    assert await store.stage_revision(revision(3, "a")) is PublicationAction.DESTROYED
    assert await gc.collect() == GcReport(3, 3, 0)


async def test_real_state_round_trip_order_and_unique_identity(mongo_publication):
    store, database = mongo_publication
    first, other = revision(), revision(resource_id="other")
    await store.ensure_indexes()
    assert await store.stage_revision(first) is PublicationAction.STAGED
    assert await store.stage_revision(first) is PublicationAction.STAGED
    assert await store.apply_revision(first) is PublicationAction.APPLIED
    assert await store.apply_revision(first) is PublicationAction.ALREADY_APPLIED
    await store.stage_revision(other)
    assert await store.get_states(["resource", "missing", "other", "resource"]) == [
        ResourcePublicationState("resource", None, first),
        ResourcePublicationState("other", other),
    ]
    collection = database["resource_publications"]
    with pytest.raises(DuplicateKeyError):
        await collection.insert_one(
            TypeAdapter(ResourcePublicationState).dump_python(
                ResourcePublicationState("resource"), mode="python"
            )
        )
    assert await collection.count_documents({}) == 2


async def test_real_initial_stage_competition_never_overwrites_higher_version(
        mongo_publication,
):
    store, _ = mongo_publication
    first, newer = revision(), revision(2)
    results = await asyncio.gather(
        store.stage_revision(first), store.stage_revision(newer), return_exceptions=True
    )
    for result in results:
        assert result in (
            PublicationAction.STAGED,
            PublicationAction.STALE,
        ) or isinstance(result, PublicationStateChangedError)
    # 若高版本输掉首次插入，由测试调用方重新提交；组件自身没有重试循环。
    assert await store.stage_revision(newer) is PublicationAction.STAGED
    assert (await store.get_states(["resource"]))[0].staged_revision == newer
    assert await store.apply_revision(first) is PublicationAction.STALE


@pytest.mark.parametrize("operation", ["stage", "apply"])
@pytest.mark.parametrize("change", ["newer", "destroyed"])
async def test_real_conditional_update_blocks_interleaved_state_change(
        mongo_publication, operation, change
):
    store, database = mongo_publication
    await store.stage_revision(revision())
    await store.apply_revision(revision())
    candidate = revision(2)
    if operation == "apply":
        await store.stage_revision(candidate)
    collection = database["resource_publications"]

    async def raced_update(*args, **kwargs):
        # 在真正执行 Mongo 条件更新前插入另一操作，确定性覆盖读写竞争窗口。
        if change == "newer":
            await store.stage_revision(revision(3))
        else:
            await store.destroy_resources(["resource"])
        return await collection.update_one(*args, **kwargs)

    racing_store = MongoPublicationStore(
        SimpleNamespace(
            find_one=collection.find_one,
            update_one=raced_update,
        )
    )
    if operation == "stage":
        action = await racing_store.stage_revision(candidate)
    else:
        action = await racing_store.apply_revision(candidate)
    expected = (
        PublicationAction.STALE if change == "newer" else PublicationAction.DESTROYED
    )
    assert action is expected
    state = (await store.get_states(["resource"]))[0]
    if change == "newer":
        assert (
                state.staged_revision == revision(3)
                and state.applied_revision == revision()
        )
    else:
        assert state == ResourcePublicationState("resource", destroyed=True)


async def test_real_destroy_upsert_is_permanent_and_resource_scoped(mongo_publication):
    store, database = mongo_publication
    await store.stage_revision(revision(resource_id="other"))
    await store.destroy_resources(["resource", "resource"])
    await store.destroy_resources(["resource"])
    assert await store.stage_revision(revision()) is PublicationAction.DESTROYED
    assert await store.apply_revision(revision()) is PublicationAction.DESTROYED
    assert await store.stage_revision(revision(100)) is PublicationAction.DESTROYED
    assert await store.get_states(["resource", "other"]) == [
        ResourcePublicationState("resource", destroyed=True),
        ResourcePublicationState("other", revision(resource_id="other")),
    ]
    assert await database["resource_publications"].count_documents({}) == 2


async def test_real_content_with_fake_projection_publishes_and_preserves_old_applied_on_failure(
        mongo_publication, document
):
    store, database = mongo_publication
    content = MongoContentStorage(
        documents=database["document_revisions"], chunks=database["doc_chunks"]
    )
    await content.ensure_indexes()
    projection = SimpleNamespace(index=AsyncMock())
    publication = DocumentPublication(
        store=store,
        reader=content,
        writer=content,
        pipeline=IngestionPipeline(
            chunker=MarkdownChunker(),
            transformers=[],
            indexers=[ChunkFactsIndexer(writer=content), projection],
        ),
    )
    first_revision = ContentRevision.create(
        resource_id=document.resource_id,
        document_version=document.document_version,
        text=document.text,
    )
    first = replace(document, content_revision=first_revision.content_revision)
    assert await publication.publish(first) is PublicationAction.APPLIED
    next_revision = ContentRevision.create(
        resource_id=document.resource_id,
        document_version=document.document_version + 1,
        text=document.text,
    )
    candidate = replace(
        first,
        document_version=next_revision.document_version,
        content_revision=next_revision.content_revision,
    )
    error = RuntimeError("fake projection failed")
    projection.index.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        await publication.publish(candidate)
    assert caught.value is error
    state = (await store.get_states([document.resource_id]))[0]
    assert (
            state.applied_revision == first_revision
            and state.staged_revision == next_revision
    )
    assert await content.get_documents(
        [(document.resource_id, next_revision.content_revision)]
    ) == [candidate]
    assert await content.get_revision_chunks(
        resource_id=document.resource_id,
        content_revision=next_revision.content_revision,
    )

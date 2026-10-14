"""资源 GC 使用真实 Mongo 内容/发布 adapter 的 BSON 往返替身，不清理业务库。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from rag.application.gc import GcReport, ResourceGarbageCollector
from rag.application.publication.models import PublicationAction
from rag.core.schema import ContentRevision


@pytest.fixture
def gc_env(publication_store, storage, read_acls):
    vectors = [SimpleNamespace(delete=AsyncMock()) for _ in range(3)]
    graph = SimpleNamespace(delete_sources=AsyncMock())
    acls = SimpleNamespace(
        delete=AsyncMock(side_effect=lambda key: read_acls.pop(key, None))
    )
    dependencies = {
        "store": publication_store,
        "content_writer": storage,
        "resource_acls": acls,
        "vector_writers": vectors,
        "graph_writers": [graph],
        "batch_size": 2,
    }
    return SimpleNamespace(
        vectors=vectors,
        graph=graph,
        acls=acls,
        dependencies=dependencies,
        collector=ResourceGarbageCollector(**dependencies),
    )


async def test_keyset_gc_removes_all_revisions_preserves_live_resource_and_markers(
    gc_env,
    publication_store,
    storage,
    make_read_document,
    make_read_chunk,
    publish_read_document,
):
    env = gc_env
    documents = [
        make_read_document(key, version=version)
        for key, version in (("c", 1), ("a", 1), ("a", 2), ("b", 1), ("live", 1))
    ]
    for document in documents:
        await publish_read_document(document)
    chunks = [make_read_chunk(document) for document in documents]
    await storage.upsert_chunks(chunks)
    await publication_store.destroy_resources(["c", "a", "b", "missing"])
    publication_store.list_destroyed_resource_ids = AsyncMock(
        wraps=publication_store.list_destroyed_resource_ids
    )
    report = await env.collector.collect()
    assert report == GcReport(scanned=4, cleaned=4)
    assert [
        call.kwargs["after_resource_id"]
        for call in publication_store.list_destroyed_resource_ids.call_args_list
    ] == [None, "b", "missing"]
    assert [
        chunk.resource_id
        for chunk in await storage.get_chunks([chunk.chunk_id for chunk in chunks])
    ] == ["live"]
    assert [
        document.resource_id
        for document in await storage.get_documents(
            [(doc.resource_id, doc.content_revision) for doc in documents]
        )
    ] == ["live"]
    assert all(
        state.destroyed
        for state in await publication_store.get_states(["a", "b", "c", "missing"])
    )
    assert (await publication_store.get_states(["live"]))[
        0
    ].applied_revision is not None
    assert (
        await publication_store.stage_revision(
            ContentRevision.create(resource_id="a", document_version=3, text="late")
        )
        is PublicationAction.DESTROYED
    )
    for writer in env.vectors:
        assert [
            call.args[0].filters[0].value for call in writer.delete.call_args_list
        ] == ["a", "b", "c", "missing"]
    # 标记不设置完成状态，重复执行仍覆盖相同资源。
    assert await env.collector.collect() == report


async def test_gc_resource_errors_have_traceback_and_low_cardinality_metric(
    gc_env, publication_store, monkeypatch
):
    counter, log = Mock(), Mock()
    monkeypatch.setattr("rag.application.gc._GC_ERRORS", counter)
    monkeypatch.setattr("rag.application.gc.error", log)
    await publication_store.destroy_resources(["resource"])
    failure = OSError("store down")
    gc_env.vectors[0].delete.side_effect = failure
    assert (await gc_env.collector.collect()).errors == 1
    counter.add.assert_called_once_with(1, {"stage": "vector"})
    assert log.call_args.kwargs["exc"] is failure
    assert failure.__traceback__ is not None


async def test_gc_scan_failure_is_counted_without_replacing_periodic_isolation(
    gc_env, monkeypatch
):
    counter = Mock()
    monkeypatch.setattr("rag.application.gc._GC_ERRORS", counter)
    gc_env.collector.collect = AsyncMock(side_effect=OSError("scan down"))
    monkeypatch.setattr(
        "rag.application.gc.asyncio.sleep",
        AsyncMock(side_effect=asyncio.CancelledError()),
    )
    with pytest.raises(asyncio.CancelledError):
        await gc_env.collector.run()
    counter.add.assert_called_once_with(1, {"stage": "scan"})


@pytest.mark.parametrize("stage", ["vector", "graph", "acl", "content"])
async def test_resource_failure_keeps_partial_progress_continues_and_rerun_recovers(
    gc_env,
    publication_store,
    storage,
    stage,
):
    env = gc_env
    await publication_store.destroy_resources(["a", "b"])
    failed = {
        "vector": env.vectors[1].delete,
        "graph": env.graph.delete_sources,
        "acl": env.acls.delete,
        "content": AsyncMock(wraps=storage.delete_resources),
    }[stage]
    if stage == "content":
        storage.delete_resources = failed
    failed.side_effect = [OSError("failed"), None]
    assert await env.collector.collect() == GcReport(2, 1, 1)
    assert failed.await_count == 2  # 不在原地重试，继续下一个资源。
    if stage == "vector":
        assert env.vectors[0].delete.await_count == 2
        assert env.vectors[2].delete.await_count == 1
    env.acls.delete.assert_any_await("b")
    failed.side_effect = None
    assert await env.collector.collect() == GcReport(2, 2, 0)
    assert all(
        state.destroyed for state in await publication_store.get_states(["a", "b"])
    )


async def test_late_content_and_acl_are_reclaimed_again(
    gc_env, publication_store, storage, make_read_document, make_read_chunk, read_acls
):
    env = gc_env
    await publication_store.destroy_resources(["late"])
    await env.collector.collect()
    doc = make_read_document("late")
    chunk = make_read_chunk(doc)
    await storage.upsert_documents([doc])
    await storage.upsert_chunks([chunk])
    read_acls["late"] = "late ACL write"
    assert await env.collector.collect() == GcReport(1, 1, 0)
    assert await storage.get_chunks([chunk.chunk_id]) == []
    assert "late" not in read_acls


async def test_empty_gc_has_no_delete_io(gc_env):
    assert await gc_env.collector.collect() == GcReport()
    gc_env.acls.delete.assert_not_awaited()
    gc_env.graph.delete_sources.assert_not_awaited()
    for writer in gc_env.vectors:
        writer.delete.assert_not_awaited()


async def test_gc_deletes_projections_and_acl_before_content(
    gc_env, publication_store, storage
):
    env = gc_env
    await publication_store.destroy_resources(["gone"])
    operations = []
    for index, writer in enumerate(env.vectors):

        async def delete(filters, index=index):
            operations.append(f"vector-{index}")
            assert filters.filters[0].key == "resource_id"
            assert filters.filters[0].value == "gone"

        writer.delete.side_effect = delete

    async def graph(filters):
        operations.append("graph")

    async def acl(resource_id):
        operations.append("acl")

    async def content(resource_ids):
        assert resource_ids == ["gone"]
        operations.append("content")

    env.graph.delete_sources.side_effect = graph
    env.acls.delete.side_effect = acl
    storage.delete_resources = AsyncMock(side_effect=content)
    assert await env.collector.collect() == GcReport(1, 1, 0)
    assert operations == ["vector-0", "vector-1", "vector-2", "graph", "acl", "content"]


async def test_scan_failure_propagates_from_collect_but_periodic_loop_resumes(gc_env):
    env = gc_env
    env.dependencies["store"].list_destroyed_resource_ids = AsyncMock(
        side_effect=OSError("scan")
    )
    with pytest.raises(OSError):
        await env.collector.collect()
    collector = ResourceGarbageCollector(**env.dependencies, interval_seconds=0.001)
    second_round = asyncio.Event()

    async def scan(**kwargs):
        if env.dependencies["store"].list_destroyed_resource_ids.await_count == 1:
            raise OSError("first scan")
        second_round.set()
        return []

    env.dependencies["store"].list_destroyed_resource_ids.reset_mock()
    env.dependencies["store"].list_destroyed_resource_ids.side_effect = scan
    task = asyncio.create_task(collector.run())
    await asyncio.wait_for(second_round.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_gc_cancellation_does_not_become_resource_error(
    gc_env, publication_store
):
    await publication_store.destroy_resources(["a"])
    gc_env.vectors[0].delete.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await gc_env.collector.collect()
    gc_env.graph.delete_sources.assert_not_awaited()


async def test_periodic_wait_starts_after_collection_finishes_without_overlap(gc_env):
    collector = ResourceGarbageCollector(**gc_env.dependencies, interval_seconds=60)
    entered, release = asyncio.Event(), asyncio.Event()

    async def collect():
        entered.set()
        await release.wait()
        return GcReport()

    collector.collect = AsyncMock(side_effect=collect)
    task = asyncio.create_task(collector.run())
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.sleep(0)
    collector.collect.assert_awaited_once()
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    collector.collect.assert_awaited_once()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

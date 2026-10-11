"""请求版本/授权决定的首次观察、不可见缓存、批量 I/O 与请求隔离。"""

import asyncio
import subprocess
import sys
from dataclasses import replace

import pytest
from common.core.domain import GroupRoleType

from rag.acl.models import GroupResourceAcl, PermissionScope, ResourceAcl
from rag.application.snapshot import ResourceSnapshot
from rag.core.schema import ContentRevision


async def test_snapshot_reader_batches_pending_resources_and_reuses_visible_decisions(
    snapshot_reader,
    read_acls,
    read_acl_repository,
    publication_store,
    make_read_document,
    publish_read_document,
):
    a = await publish_read_document(make_read_document("a"))
    b = await publish_read_document(make_read_document("b"))
    for resource_id in ("a", "b"):
        read_acls[resource_id] = ResourceAcl(resource_id, 1, "reader")
    snapshot = ResourceSnapshot(PermissionScope("reader"))
    assert await snapshot_reader.read_documents(["b", "a", "b"], snapshot=snapshot) == [
        b,
        a,
    ]
    publication_store.get_states.assert_awaited_once_with(["b", "a"])
    read_acl_repository.get_resource_acls.assert_awaited_once_with(["b", "a"])
    assert await snapshot_reader.read_documents(["a", "b", "a"], snapshot=snapshot) == [
        a,
        b,
    ]
    assert publication_store.get_states.await_count == 1
    assert read_acl_repository.get_resource_acls.await_count == 1

    c = await publish_read_document(make_read_document("c"))
    read_acls["c"] = ResourceAcl("c", 1, "reader")
    assert await snapshot_reader.read_documents(
        ["a", "c", "b", "c"], snapshot=snapshot
    ) == [a, c, b]
    publication_store.get_states.assert_awaited_with(["c"])
    read_acl_repository.get_resource_acls.assert_awaited_with(["c"])
    assert publication_store.get_states.await_count == 2
    assert read_acl_repository.get_resource_acls.await_count == 2


@pytest.mark.parametrize(
    "invisible", ["missing", "staged", "destroyed", "missing-acl", "denied"]
)
@pytest.mark.parametrize("read_kind", ["documents", "chunks"])
async def test_invisible_decisions_are_cached_and_do_not_block_visible_resources(
    snapshot_reader,
    storage,
    read_acls,
    read_acl_repository,
    publication_store,
    make_read_document,
    make_read_chunk,
    publish_read_document,
    invisible,
    read_kind,
):
    visible = await publish_read_document(make_read_document("visible"))
    read_acls["visible"] = ResourceAcl("visible", 1, "reader")
    hidden_doc = make_read_document("hidden")
    hidden = ContentRevision.create(
        resource_id="hidden", document_version=1, text=hidden_doc.text
    )
    if invisible == "staged":
        await publication_store.stage_revision(hidden)
    elif invisible == "destroyed":
        await publish_read_document(hidden_doc)
        await publication_store.destroy_resources(["hidden"])
    elif invisible in ("missing-acl", "denied"):
        await publish_read_document(hidden_doc)
    if invisible == "denied":
        read_acls["hidden"] = ResourceAcl("hidden", 1, "another-user")
    snapshot = ResourceSnapshot(PermissionScope("reader"))
    if read_kind == "documents":
        operation = snapshot_reader.read_documents
        ids = ["hidden", "visible"]
        expected = [visible]
    else:
        hidden_chunk, visible_chunk = (
            make_read_chunk(hidden_doc),
            make_read_chunk(visible),
        )
        await storage.upsert_chunks([hidden_chunk, visible_chunk])
        operation = snapshot_reader.read_chunks
        ids = [hidden_chunk.chunk_id, visible_chunk.chunk_id]
        expected = [visible_chunk]
    assert await operation(ids, snapshot=snapshot) == expected
    queried_acls = read_acl_repository.get_resource_acls.call_args.args[0]
    assert ("hidden" in queried_acls) is (invisible in ("missing-acl", "denied"))
    assert await operation(ids, snapshot=snapshot) == expected
    assert publication_store.get_states.await_count == 1
    assert read_acl_repository.get_resource_acls.await_count == 1


@pytest.mark.parametrize("initial", ["missing", "staged", "missing-acl", "denied"])
async def test_initially_invisible_resource_requires_new_snapshot_to_become_visible(
    snapshot_reader,
    read_acls,
    publication_store,
    make_read_document,
    publish_read_document,
    initial,
):
    document = make_read_document()
    revision = ContentRevision.create(
        resource_id="resource", document_version=1, text=document.text
    )
    if initial == "staged":
        await publication_store.stage_revision(revision)
    elif initial in ("missing-acl", "denied"):
        await publish_read_document(document)
    if initial == "denied":
        read_acls["resource"] = ResourceAcl("resource", 1, "other")
    old = ResourceSnapshot(PermissionScope("reader"))
    assert await snapshot_reader.read_documents(["resource"], snapshot=old) == []
    await publish_read_document(document)
    read_acls["resource"] = ResourceAcl("resource", 2, "reader")
    assert await snapshot_reader.read_documents(["resource"], snapshot=old) == []
    assert await snapshot_reader.read_documents(
        ["resource"], snapshot=ResourceSnapshot(PermissionScope("reader"))
    ) == [document]


@pytest.mark.parametrize(
    "change", ["new-applied", "staged-only", "acl-revoked", "destroyed"]
)
async def test_selected_revision_and_visibility_survive_later_changes(
    snapshot_reader,
    read_acls,
    publication_store,
    make_read_document,
    publish_read_document,
    change,
):
    first = await publish_read_document(make_read_document())
    read_acls["resource"] = ResourceAcl("resource", 1, "reader")
    old = ResourceSnapshot(PermissionScope("reader"))
    assert await snapshot_reader.read_documents(["resource"], snapshot=old) == [first]
    expected = first
    if change == "new-applied":
        expected = await publish_read_document(
            make_read_document(version=2, text="新版")
        )
    elif change == "staged-only":
        await publication_store.stage_revision(
            ContentRevision.create(
                resource_id="resource", document_version=2, text="新版"
            )
        )
    elif change == "acl-revoked":
        read_acls["resource"] = replace(read_acls["resource"], owner_id="other")
    else:
        await publication_store.destroy_resources(["resource"])
    assert await snapshot_reader.read_documents(["resource"], snapshot=old) == [first]
    fresh = await snapshot_reader.read_documents(
        ["resource"], snapshot=ResourceSnapshot(PermissionScope("reader"))
    )
    assert fresh == ([] if change in ("acl-revoked", "destroyed") else [expected])


@pytest.mark.parametrize(
    "scope,acl,allowed",
    [
        (
            PermissionScope("reader"),
            ResourceAcl("resource", 1, "reader", excluded_read_users=["reader"]),
            True,
        ),
        (
            PermissionScope("reader"),
            ResourceAcl(
                "resource",
                1,
                "other",
                readable_users=["reader"],
                excluded_read_users=["reader"],
            ),
            True,
        ),
        (
            PermissionScope("reader", {"g": GroupRoleType.ADMIN}),
            ResourceAcl(
                "resource",
                1,
                "other",
                excluded_read_users=["reader"],
                group_acls=[GroupResourceAcl("g", True)],
            ),
            False,
        ),
        (
            PermissionScope("reader", {"g": GroupRoleType.ADMIN}),
            ResourceAcl(
                "resource", 1, "other", group_acls=[GroupResourceAcl("g", False)]
            ),
            True,
        ),
        (
            PermissionScope("reader", {"g": GroupRoleType.MEMBER}),
            ResourceAcl(
                "resource", 1, "other", group_acls=[GroupResourceAcl("g", True)]
            ),
            True,
        ),
    ],
)
async def test_authorization_uses_existing_resource_acl_rules(
    snapshot_reader,
    read_acls,
    make_read_document,
    publish_read_document,
    scope,
    acl,
    allowed,
):
    document = await publish_read_document(make_read_document())
    read_acls["resource"] = acl
    assert await snapshot_reader.read_documents(
        ["resource"], snapshot=ResourceSnapshot(scope)
    ) == ([document] if allowed else [])


async def test_empty_and_unpublished_batches_do_not_query_acl(
    snapshot_reader,
    read_acl_repository,
    publication_store,
):
    snapshot = ResourceSnapshot(PermissionScope("reader"))
    assert await snapshot_reader.read_documents([], snapshot=snapshot) == []
    publication_store.get_states.assert_not_awaited()
    assert await snapshot_reader.read_documents(["missing"], snapshot=snapshot) == []
    read_acl_repository.get_resource_acls.assert_not_awaited()


@pytest.mark.parametrize("failed_backend", ["publication", "acl"])
async def test_io_failure_propagates_without_caching_invisibility(
    snapshot_reader,
    read_acls,
    read_acl_repository,
    publication_store,
    make_read_document,
    publish_read_document,
    failed_backend,
):
    document = await publish_read_document(make_read_document())
    read_acls["resource"] = ResourceAcl("resource", 1, "reader")
    method = (
        publication_store.get_states
        if failed_backend == "publication"
        else read_acl_repository.get_resource_acls
    )
    original = method.side_effect
    method.side_effect = OSError("database unavailable")
    snapshot = ResourceSnapshot(PermissionScope("reader"))
    with pytest.raises(OSError):
        await snapshot_reader.read_documents(["resource"], snapshot=snapshot)
    method.side_effect = original
    assert await snapshot_reader.read_documents(["resource"], snapshot=snapshot) == [
        document
    ]


async def test_shared_snapshot_reader_keeps_concurrent_users_isolated(
    snapshot_reader,
    read_acls,
    read_acl_repository,
    make_read_document,
    publish_read_document,
):
    document = await publish_read_document(make_read_document())
    read_acls["resource"] = ResourceAcl("resource", 1, "reader")
    arrived = 0
    both = asyncio.Event()

    async def read_acls_together(resource_ids):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both.set()
        await both.wait()
        return read_acls

    read_acl_repository.get_resource_acls.side_effect = read_acls_together
    allowed = ResourceSnapshot(PermissionScope("reader"))
    denied = ResourceSnapshot(PermissionScope("other"))
    results = await asyncio.wait_for(
        asyncio.gather(
            snapshot_reader.read_documents(["resource"], snapshot=allowed),
            snapshot_reader.read_documents(["resource"], snapshot=denied),
        ),
        timeout=2,
    )
    assert results == [[document], []]
    assert await snapshot_reader.read_documents(["resource"], snapshot=allowed) == [
        document
    ]
    assert await snapshot_reader.read_documents(["resource"], snapshot=denied) == []


async def test_interleaved_resolutions_do_not_overwrite_completed_selection(
    snapshot_reader,
    read_acls,
    read_acl_repository,
    make_read_document,
    publish_read_document,
):
    await publish_read_document(make_read_document())
    read_acls["resource"] = ResourceAcl("resource", 1, "reader")
    waiting = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def read_acls_interleaved(resource_ids):
        nonlocal calls
        calls += 1
        if calls == 1:
            waiting.set()
            await release.wait()
        return read_acls

    read_acl_repository.get_resource_acls.side_effect = read_acls_interleaved
    snapshot = ResourceSnapshot(PermissionScope("reader"))
    delayed = asyncio.create_task(
        snapshot_reader.read_documents(["resource"], snapshot=snapshot)
    )
    try:
        await asyncio.wait_for(waiting.wait(), timeout=2)
        newest = await publish_read_document(make_read_document(version=2, text="新版"))
        assert await snapshot_reader.read_documents(
            ["resource"], snapshot=snapshot
        ) == [newest]
        release.set()
        assert await asyncio.wait_for(delayed, timeout=2) == [newest]
    finally:
        release.set()
        delayed.cancel()
        await asyncio.gather(delayed, return_exceptions=True)


def test_snapshot_module_does_not_import_retrieval_use_cases_or_runtime_config():
    code = """
import sys
from rag.application.snapshot import ResourceSnapshot, ResourceSnapshotReader
assert not any(name.startswith(('rag.reading', 'rag.retrieval', 'rag.application.retrieval',
    'rag.core.config')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-c", code], check=True)

"""内容读取的资源级授权、版本回查、部分结果与共享请求决定。"""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from rag.domain.models.acl import PermissionScope, ResourceAcl
from rag.application.common.snapshot import ResourceSnapshot


async def test_documents_omit_invisible_and_missing_facts_preserving_order(
        snapshot_reader,
        storage,
        read_acls,
        make_read_document,
        publish_read_document,
):
    a, b, denied, missing = [
        make_read_document(resource_id)
        for resource_id in ("a", "b", "denied", "missing-facts")
    ]
    for document in (a, b, denied, missing):
        await publish_read_document(document)
        read_acls[document.resource_id] = ResourceAcl(
            document.resource_id, 1, "reader" if document is not denied else "other"
        )
    await storage.delete_revisions([("missing-facts", missing.content_revision)])
    storage.get_documents = AsyncMock(wraps=storage.get_documents)
    result = await snapshot_reader.read_documents(
        ["b", "denied", "not-published", "missing-facts", "a", "b"],
        snapshot=ResourceSnapshot(PermissionScope("reader")),
    )
    assert result == [b, a]
    storage.get_documents.assert_awaited_once_with(
        [
            ("b", b.content_revision),
            ("missing-facts", missing.content_revision),
            ("a", a.content_revision),
        ]
    )


async def test_chunks_inherit_resource_permission_and_match_selected_revision(
        snapshot_reader,
        storage,
        read_acls,
        make_read_document,
        make_read_chunk,
        publish_read_document,
):
    first, newest, denied = (
        make_read_document(),
        make_read_document(version=2, text="新版"),
        make_read_document("denied"),
    )
    for document in (first, newest, denied):
        await publish_read_document(document)
    read_acls["resource"] = ResourceAcl("resource", 1, "reader")
    read_acls["denied"] = ResourceAcl("denied", 1, "other")
    stale, current_a, current_b, hidden = (
        make_read_chunk(first),
        make_read_chunk(newest),
        make_read_chunk(newest, 1),
        make_read_chunk(denied),
    )
    await storage.upsert_chunks([stale, current_a, current_b, hidden])
    result = await snapshot_reader.read_chunks(
        [
            current_b.chunk_id,
            stale.chunk_id,
            hidden.chunk_id,
            "missing",
            current_a.chunk_id,
            current_b.chunk_id,
        ],
        snapshot=ResourceSnapshot(PermissionScope("reader")),
    )
    assert result == [current_b, current_a]


@pytest.mark.parametrize("first_read", ["documents", "chunks"])
async def test_document_and_chunk_suboperations_share_snapshot(
        snapshot_reader,
        storage,
        read_acls,
        read_acl_repository,
        publication_store,
        make_read_document,
        make_read_chunk,
        publish_read_document,
        first_read,
):
    document = make_read_document()
    chunk = make_read_chunk(document)
    await publish_read_document(document)
    await storage.upsert_chunks([chunk])
    read_acls["resource"] = ResourceAcl("resource", 1, "reader")
    snapshot = ResourceSnapshot(PermissionScope("reader"))
    if first_read == "documents":
        assert await snapshot_reader.read_documents(
            ["resource"], snapshot=snapshot
        ) == [document]
        assert await snapshot_reader.read_chunks(
            [chunk.chunk_id], snapshot=snapshot
        ) == [chunk]
    else:
        assert await snapshot_reader.read_chunks(
            [chunk.chunk_id], snapshot=snapshot
        ) == [chunk]
        assert await snapshot_reader.read_documents(
            ["resource"], snapshot=snapshot
        ) == [document]
    publication_store.get_states.assert_awaited_once_with(["resource"])
    read_acl_repository.get_resource_acls.assert_awaited_once_with(["resource"])


async def test_document_read_during_apply_change_uses_pinned_revision(
        snapshot_reader,
        storage,
        read_acls,
        make_read_document,
        publish_read_document,
):
    first, newest = make_read_document(), make_read_document(version=2, text="新版")
    await publish_read_document(first)
    read_acls["resource"] = ResourceAcl("resource", 1, "reader")
    original = storage.get_documents

    async def read_after_apply(keys):
        await publish_read_document(newest)
        return await original(keys)

    storage.get_documents = AsyncMock(side_effect=read_after_apply)
    old = ResourceSnapshot(PermissionScope("reader"))
    assert await snapshot_reader.read_documents(["resource"], snapshot=old) == [first]
    storage.get_documents.assert_awaited_once_with(
        [("resource", first.content_revision)]
    )
    storage.get_documents = AsyncMock(wraps=original)
    assert await snapshot_reader.read_documents(
        ["resource"], snapshot=ResourceSnapshot(PermissionScope("reader"))
    ) == [newest]


async def test_chunk_read_after_apply_change_does_not_mix_versions(
        snapshot_reader,
        storage,
        read_acls,
        make_read_document,
        make_read_chunk,
        publish_read_document,
):
    first, newest = make_read_document(), make_read_document(version=2, text="新版")
    await publish_read_document(first)
    read_acls["resource"] = ResourceAcl("resource", 1, "reader")
    old = ResourceSnapshot(PermissionScope("reader"))
    assert await snapshot_reader.read_documents(["resource"], snapshot=old) == [first]
    await publish_read_document(newest)
    old_chunk, new_chunk = make_read_chunk(first), make_read_chunk(newest)
    await storage.upsert_chunks([old_chunk, new_chunk])
    ids = [new_chunk.chunk_id, old_chunk.chunk_id]
    assert await snapshot_reader.read_chunks(ids, snapshot=old) == [old_chunk]
    assert await snapshot_reader.read_chunks(
        ids, snapshot=ResourceSnapshot(PermissionScope("reader"))
    ) == [new_chunk]


@pytest.mark.parametrize(
    "changed_field,value",
    [
        ("resource_id", "unexpected"),
        ("content_revision", "wrong"),
        ("document_version", 99),
        ("text", "正文被改写"),
    ],
)
async def test_bad_document_identity_does_not_block_other_documents(
        snapshot_reader,
        storage,
        read_acls,
        make_read_document,
        publish_read_document,
        changed_field,
        value,
):
    a, b = make_read_document("a"), make_read_document("b")
    for document in (a, b):
        await publish_read_document(document)
        read_acls[document.resource_id] = ResourceAcl(document.resource_id, 1, "reader")
    # 替换存储返回边界，以覆盖字段标签与正文身份不一致的持久化数据。
    storage.get_documents = AsyncMock(
        return_value=[replace(a, **{changed_field: value}), b]
    )
    assert await snapshot_reader.read_documents(
        ["a", "b"], snapshot=ResourceSnapshot(PermissionScope("reader"))
    ) == [b]


async def test_missing_pinned_facts_do_not_switch_to_new_revision(
        snapshot_reader,
        storage,
        read_acls,
        make_read_document,
        make_read_chunk,
        publish_read_document,
):
    first, newest = make_read_document(), make_read_document(version=2, text="新版")
    await publish_read_document(first)
    read_acls["resource"] = ResourceAcl("resource", 1, "reader")
    snapshot = ResourceSnapshot(PermissionScope("reader"))
    assert await snapshot_reader.read_documents(["resource"], snapshot=snapshot) == [
        first
    ]
    await publish_read_document(newest)
    chunk = make_read_chunk(newest)
    await storage.upsert_chunks([chunk])
    await storage.delete_revisions([("resource", first.content_revision)])
    assert await snapshot_reader.read_documents(["resource"], snapshot=snapshot) == []
    assert await snapshot_reader.read_chunks([chunk.chunk_id], snapshot=snapshot) == []
    fresh = ResourceSnapshot(PermissionScope("reader"))
    assert await snapshot_reader.read_documents(["resource"], snapshot=fresh) == [
        newest
    ]


async def test_destroyed_or_revoked_after_selection_is_observed_by_new_call(
        snapshot_reader,
        storage,
        read_acls,
        publication_store,
        make_read_document,
        make_read_chunk,
        publish_read_document,
):
    document = make_read_document()
    chunk = make_read_chunk(document)
    await publish_read_document(document)
    await storage.upsert_chunks([chunk])
    read_acls["resource"] = ResourceAcl("resource", 1, "reader")
    snapshot = ResourceSnapshot(PermissionScope("reader"))
    assert await snapshot_reader.read_documents(["resource"], snapshot=snapshot) == [
        document
    ]
    await publication_store.destroy_resources(["resource"])
    del read_acls["resource"]
    assert await snapshot_reader.read_documents(["resource"], snapshot=snapshot) == [
        document
    ]
    assert await snapshot_reader.read_chunks([chunk.chunk_id], snapshot=snapshot) == [
        chunk
    ]
    fresh = ResourceSnapshot(PermissionScope("reader"))
    assert await snapshot_reader.read_documents(["resource"], snapshot=fresh) == []
    assert await snapshot_reader.read_chunks([chunk.chunk_id], snapshot=fresh) == []


async def test_empty_and_missing_chunks_have_no_state_or_acl_io(
        snapshot_reader,
        storage,
        read_acl_repository,
        publication_store,
        collections,
):
    snapshot = ResourceSnapshot(PermissionScope("reader"))
    assert await snapshot_reader.read_documents([], snapshot=snapshot) == []
    assert await snapshot_reader.read_chunks([], snapshot=snapshot) == []
    for collection in collections:
        collection.find.assert_not_called()
    assert await snapshot_reader.read_chunks(["missing"], snapshot=snapshot) == []
    publication_store.get_states.assert_not_awaited()
    read_acl_repository.get_resource_acls.assert_not_awaited()


@pytest.mark.parametrize("method", ["documents", "chunks"])
@pytest.mark.parametrize("exception", [OSError, ValueError])
async def test_content_io_and_decoding_errors_propagate(
        snapshot_reader,
        storage,
        read_acls,
        make_read_document,
        make_read_chunk,
        publish_read_document,
        method,
        exception,
):
    document = make_read_document()
    await publish_read_document(document)
    read_acls["resource"] = ResourceAcl("resource", 1, "reader")
    error = exception("content backend failed")
    if method == "documents":
        storage.get_documents = AsyncMock(side_effect=error)
        operation = snapshot_reader.read_documents
        ids = ["resource"]
    else:
        storage.get_chunks = AsyncMock(side_effect=error)
        operation = snapshot_reader.read_chunks
        ids = [make_read_chunk(document).chunk_id]
    with pytest.raises(exception) as caught:
        await operation(ids, snapshot=ResourceSnapshot(PermissionScope("reader")))
    assert caught.value is error


async def test_shared_reading_use_case_serves_distinct_users_without_request_state(
        snapshot_reader,
        read_acls,
        make_read_document,
        publish_read_document,
):
    document = make_read_document()
    await publish_read_document(document)
    read_acls["resource"] = ResourceAcl("resource", 1, "reader")
    results = await asyncio.gather(
        snapshot_reader.read_documents(
            ["resource"], snapshot=ResourceSnapshot(PermissionScope("reader"))
        ),
        snapshot_reader.read_documents(
            ["resource"], snapshot=ResourceSnapshot(PermissionScope("other"))
        ),
    )
    assert results == [[document], []]

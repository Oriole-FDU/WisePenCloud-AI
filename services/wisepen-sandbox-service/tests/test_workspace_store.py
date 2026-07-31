from __future__ import annotations

import pytest
from common.core.exceptions import ServiceException

from sandbox.core.storage.local import LocalWorkspaceStore
from sandbox.core.storage.mongo import MongoWorkspaceStore
from sandbox.domain.entities import WorkspaceSnapshot
from sandbox.domain.error_codes import SandboxErrorCode


@pytest.mark.asyncio
async def test_local_store_replaces_complete_snapshot_and_preserves_binary(tmp_path):
    store = LocalWorkspaceStore(str(tmp_path))
    await store.commit(
        WorkspaceSnapshot(
            "tenant", "workspace", {"old.txt": "old", "blob.bin": b"\xff\x00"}
        ),
        "lease-1",
        1,
    )
    await store.commit(
        WorkspaceSnapshot("tenant", "workspace", {"new.txt": "new"}),
        "lease-2",
        2,
    )
    assert (await store.snapshot("tenant", "workspace")).files == {"new.txt": "new"}

    await store.commit(WorkspaceSnapshot("tenant", "workspace"), "lease-2", 2)
    assert (await store.snapshot("tenant", "workspace")).files == {}


@pytest.mark.asyncio
async def test_local_store_rejects_stale_fencing(tmp_path):
    store = LocalWorkspaceStore(str(tmp_path))
    await store.commit(
        WorkspaceSnapshot("tenant", "workspace", {"new": "value"}), "lease-2", 2
    )
    with pytest.raises(ServiceException) as exc_info:
        await store.commit(
            WorkspaceSnapshot("tenant", "workspace", {"old": "value"}),
            "lease-1",
            1,
        )
    assert exc_info.value.code == SandboxErrorCode.FENCING_REJECTED.code
    assert (await store.snapshot("tenant", "workspace")).files == {"new": "value"}


class FakeCollection:
    def __init__(self):
        self.document = None

    def create_index(self, *args, **kwargs):
        return None

    def find_one(self, query, projection=None):
        if not self.document:
            return None
        if self.document["tenant_id"] != query["tenant_id"]:
            return None
        if self.document["workspace_id"] != query["workspace_id"]:
            return None
        if projection:
            return {key: self.document[key] for key in projection if key in self.document}
        return dict(self.document)

    def replace_one(self, query, document, upsert=False):
        self.document = dict(document)


class FakeDatabase:
    def __init__(self, collection):
        self.collection = collection

    def __getitem__(self, name):
        return self.collection


class FakeMongoClient:
    def __init__(self):
        self.collection = FakeCollection()

    def __getitem__(self, name):
        return FakeDatabase(self.collection)


@pytest.mark.asyncio
async def test_mongo_store_binary_empty_replacement_and_fencing():
    client = FakeMongoClient()
    store = MongoWorkspaceStore(client=client)
    await store.commit(
        WorkspaceSnapshot("tenant", "workspace", {"blob.bin": b"\xff", "a": "text"}),
        "lease-2",
        2,
    )
    assert (await store.snapshot("tenant", "workspace")).files == {
        "blob.bin": b"\xff",
        "a": "text",
    }
    with pytest.raises(ServiceException) as exc_info:
        await store.commit(WorkspaceSnapshot("tenant", "workspace"), "lease-1", 1)
    assert exc_info.value.code == SandboxErrorCode.FENCING_REJECTED.code

    await store.commit(WorkspaceSnapshot("tenant", "workspace"), "lease-3", 3)
    assert (await store.snapshot("tenant", "workspace")).files == {}

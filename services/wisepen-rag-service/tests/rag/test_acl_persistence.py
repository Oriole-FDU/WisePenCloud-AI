from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from rag.core.persistence.mongo.authoritative_acl_reader import (
    AuthoritativeAclError,
    _AuthoritativeAclProjector,
    MongoAuthoritativeAclReader,
)
from rag.core.persistence.mongo.resource_acl_store import MongoResourceAclStore
from rag.domain.entities.rag_acl import ResourceAclEntity
from rag.domain.models.acl import GroupResourceAcl, ResourceAcl


class _Collection:
    def __init__(self, record):
        self.record = record
        self.request = None

    async def find_one(self, query):
        self.request = query
        return self.record


@pytest.mark.asyncio
async def test_authoritative_acl_reader_maps_resource_and_group_view_rules() -> None:
    resource_id = str(ObjectId())
    collection = _Collection(
        {
            "_id": ObjectId(resource_id),
            "ownerId": " owner-1 ",
            "updateTime": datetime(2026, 1, 2, tzinfo=UTC),
            "specifiedUsersGrantedActionsMask": {
                "user-1": 2,
                "user-2": 0,
            },
            "computedGroupAcls": {
                "group-1": {
                    "baseMask": 2,
                    "userMasks": {"user-3": 0},
                },
                "group-2": {
                    "baseMask": 0,
                    "userMasks": {"user-4": 2},
                },
            },
        }
    )

    acl = await MongoAuthoritativeAclReader(collection=collection).get_resource_acl(
        resource_id
    )

    assert acl is not None
    assert acl.resource_id == resource_id
    assert acl.owner_id == "owner-1"
    assert acl.readable_users == ["user-1"]
    assert acl.excluded_read_users == ["user-2"]
    assert acl.group_acls == [
        GroupResourceAcl(
            group_id="group-1",
            default_readable=True,
            excluded_read_users=["user-3"],
        ),
        GroupResourceAcl(
            group_id="group-2",
            default_readable=False,
            readable_users=["user-4"],
        ),
    ]


@pytest.mark.asyncio
async def test_authoritative_acl_reader_returns_none_for_missing_resource() -> None:
    collection = _Collection(None)
    resource_id = str(ObjectId())

    assert (
        await MongoAuthoritativeAclReader(collection=collection).get_resource_acl(
            resource_id
        )
        is None
    )


@pytest.mark.asyncio
async def test_authoritative_acl_reader_rejects_invalid_resource_id() -> None:
    with pytest.raises(AuthoritativeAclError):
        await MongoAuthoritativeAclReader(
            collection=_Collection(None)
        ).get_resource_acl("not-an-object-id")


def test_authoritative_acl_projector_projects_record_without_reader_io() -> None:
    projector = _AuthoritativeAclProjector()

    acl = projector.project(
        {
            "ownerId": " owner-1 ",
            "updateTime": datetime(2026, 1, 2, tzinfo=UTC),
            "specifiedUsersGrantedActionsMask": {"user-1": 2},
        },
        "resource-1",
    )

    assert acl == ResourceAcl(
        resource_id="resource-1",
        acl_revision=int(datetime(2026, 1, 2, tzinfo=UTC).timestamp() * 1000),
        owner_id="owner-1",
        readable_users=["user-1"],
    )


@pytest.mark.asyncio
async def test_resource_acl_store_reads_one_acl_directly(monkeypatch) -> None:
    resource_id = "resource-1"
    entity = SimpleNamespace(
        resource_id=resource_id,
        acl_revision=3,
        owner_id="owner-1",
        readable_users=["user-1"],
        excluded_read_users=[],
        group_acls=[
            SimpleNamespace(
                group_id="group-1",
                is_readable=True,
                readable_users=[],
                excluded_read_users=[],
            )
        ],
    )
    queries = []

    async def find_one(query):
        queries.append(query)
        return entity

    monkeypatch.setattr(ResourceAclEntity, "find_one", find_one)

    acl = await MongoResourceAclStore().get_resource_acl(resource_id)

    assert acl is not None
    assert acl.resource_id == resource_id
    assert acl.acl_revision == 3
    assert acl.readable_users == ["user-1"]
    assert acl.group_acls == [
        GroupResourceAcl(group_id="group-1", default_readable=True)
    ]
    assert len(queries) == 1


@pytest.mark.asyncio
async def test_resource_acl_store_returns_none_for_missing_acl(monkeypatch) -> None:
    async def find_one(query):
        return None

    monkeypatch.setattr(ResourceAclEntity, "find_one", find_one)

    assert await MongoResourceAclStore().get_resource_acl("missing") is None


@pytest.mark.asyncio
async def test_resource_acl_store_recovers_from_concurrent_upsert(monkeypatch) -> None:
    class _Result:
        def __init__(self, *, matched_count=0, upserted_id=None) -> None:
            self.matched_count = matched_count
            self.upserted_id = upserted_id

    class _PymongoCollection:
        def __init__(self) -> None:
            self.calls = []

        async def update_one(self, query, update, upsert=False):
            self.calls.append((query, update, upsert))
            if len(self.calls) == 1:
                raise DuplicateKeyError("resource inserted concurrently")
            return _Result(matched_count=1)

    collection = _PymongoCollection()

    monkeypatch.setattr(
        ResourceAclEntity,
        "get_pymongo_collection",
        lambda: collection,
    )

    saved = await MongoResourceAclStore().save_if_newer(
        ResourceAcl(
            resource_id="resource-1",
            acl_revision=7,
            owner_id="owner-1",
        )
    )

    assert saved is True
    assert collection.calls[0][2] is True
    assert collection.calls[1][2] is False
    assert collection.calls[1][0]["resource_id"] == "resource-1"

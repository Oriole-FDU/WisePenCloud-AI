from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from bson import ObjectId
from rag.application.rag.acl import (
    RagAclProjectionError,
    RagAclProjector,
    RagResourceAclProjection,
)
from rag.application.rag.kafka_consumers import (
    RagAclRecalculateConsumer,
)
from rag.core.persistence.mongo.acl import (
    MongoRagAclProjectionRepository,
)
from rag.domain.entities.rag_acl import RagAclProjectionDocument


def test_projector_preserves_resource_user_override_priority() -> None:
    update_time = datetime(2026, 7, 26, 8, 30, tzinfo=timezone.utc)
    projection = RagAclProjector().from_resource_item(
        {
            "_id": "res-1",
            "ownerId": "owner-1",
            "updateTime": update_time,
            "specifiedUsersGrantedActionsMask": {
                "reader": 2,
                "explicitly-denied": 1,
            },
            "computedGroupAcls": {},
        }
    )

    assert projection.readable_users == ("reader",)
    assert projection.excluded_read_users == ("explicitly-denied",)
    assert projection.acl_revision == int(update_time.timestamp() * 1000)


def test_projector_uses_computed_group_acl_view_bit() -> None:
    projection = RagAclProjector().from_resource_item(
        {
            "_id": "res-1",
            "ownerId": "owner-1",
            "updateTime": datetime(2026, 7, 26, tzinfo=timezone.utc),
            "computedGroupAcls": {
                "group-readable": {
                    "baseMask": 3,
                    "userMasks": {"blocked": 1, "still-readable": 2},
                },
                "group-private": {
                    "baseMask": 1,
                    "userMasks": {"granted": 2, "still-private": 1},
                },
            },
        }
    )

    readable, private = projection.computed_group_acls
    assert readable.is_readable
    assert readable.excluded_read_users == ("blocked",)
    assert readable.readable_users == ()
    assert not private.is_readable
    assert private.readable_users == ("granted",)
    assert private.excluded_read_users == ()


def test_projector_ignores_invalid_external_acl_values() -> None:
    projection = RagAclProjector().from_resource_item(
        {
            "_id": "res-1",
            "ownerId": "owner-1",
            "updateTime": datetime(2026, 7, 26, tzinfo=timezone.utc),
            "specifiedUsersGrantedActionsMask": {
                "reader": 2,
                3: 2,
                "string-mask": "2",
                "bool-mask": True,
            },
            "computedGroupAcls": {
                "valid-group": {"baseMask": "2", "userMasks": {"reader": 2}},
                4: {"baseMask": 2},
                "invalid-acl": [],
            },
        }
    )

    assert projection.readable_users == ("reader",)
    assert len(projection.computed_group_acls) == 1
    assert projection.computed_group_acls[0].group_id == "valid-group"
    assert not projection.computed_group_acls[0].is_readable


@pytest.mark.asyncio
async def test_acl_consumer_rejects_invalid_resource_id() -> None:
    consumer = RagAclRecalculateConsumer(
        repository=_RecordingRepository(source=None),
        projection_targets=(),
    )

    with pytest.raises(RagAclProjectionError):
        await consumer.handle({"resourceId": 1})


@pytest.mark.asyncio
async def test_consumer_always_reads_authoritative_resource_projection() -> None:
    repository = _RecordingRepository(
        source=RagResourceAclProjection(
            resource_id="res-1",
            acl_revision=1,
            owner_id="owner-from-resource-db",
        )
    )
    target = _RecordingProjectionTarget()
    consumer = RagAclRecalculateConsumer(
        repository=repository,
        projection_targets=(target,),
    )

    await consumer.handle(
        {
            "resourceId": "res-1",
            "ownerId": "untrusted-message-field",
            "triggerSource": "RESOURCE_ACTION_PERMISSION_CHANGED",
        }
    )

    assert repository.load_calls == ["res-1"]
    assert repository.saved is repository.source
    assert target.saved == [repository.source]


@pytest.mark.asyncio
async def test_repository_reads_java_resource_collection(monkeypatch) -> None:
    resource_id = ObjectId()
    collection = _FakeProjectionCollection(
        databases={
            "wisepen_res_permission": {
                "wisepen_resource_items": _FakeResourceCollection(
                    {
                        "_id": resource_id,
                        "ownerId": "owner-1",
                        "updateTime": datetime(
                            2026, 7, 26, tzinfo=timezone.utc
                        ),
                        "specifiedUsersGrantedActionsMask": {"reader": 2},
                    }
                )
            }
        }
    )
    monkeypatch.setattr(
        RagAclProjectionDocument,
        "get_pymongo_collection",
        classmethod(lambda cls: collection),
    )
    repository = MongoRagAclProjectionRepository(
        projector=RagAclProjector(),
        resource_database_name="wisepen_res_permission",
    )

    projection = await repository.load_authoritative_projection(str(resource_id))

    assert projection is not None
    assert projection.resource_id == str(resource_id)
    assert projection.readable_users == ("reader",)
    resource_collection = collection.database.client["wisepen_res_permission"][
        "wisepen_resource_items"
    ]
    assert resource_collection.queries == [{"_id": resource_id}]


class _RecordingRepository:
    def __init__(self, *, source: RagResourceAclProjection | None) -> None:
        self.source = source
        self.saved: RagResourceAclProjection | None = None
        self.load_calls: list[str] = []
        self.upsert_calls = 0

    async def load_authoritative_projection(
        self,
        resource_id: str,
    ) -> RagResourceAclProjection | None:
        self.load_calls.append(resource_id)
        return self.source

    async def upsert_projection(self, projection: RagResourceAclProjection) -> None:
        self.upsert_calls += 1
        self.saved = projection

    async def get_projection(self, resource_id: str) -> RagResourceAclProjection | None:
        return (
            self.saved if self.saved and self.saved.resource_id == resource_id else None
        )


class _RecordingProjectionTarget:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.saved: list[RagResourceAclProjection] = []
        self.fail_once = fail_once

    async def update_acl_projection(
        self,
        projection: RagResourceAclProjection,
    ) -> None:
        self.saved.append(projection)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("target unavailable")


@pytest.mark.asyncio
async def test_acl_consumer_replays_targets_after_partial_failure() -> None:
    repository = _RecordingRepository(
        source=RagResourceAclProjection(
            resource_id="res-1",
            acl_revision=1,
            owner_id="owner-1",
        )
    )
    first = _RecordingProjectionTarget()
    second = _RecordingProjectionTarget(fail_once=True)
    consumer = RagAclRecalculateConsumer(
        repository=repository,
        projection_targets=(first, second),
    )
    payload = {"resourceId": "res-1"}

    with pytest.raises(RuntimeError, match="target unavailable"):
        await consumer.handle(payload)
    await consumer.handle(payload)

    assert repository.upsert_calls == 2
    assert len(first.saved) == 2
    assert len(second.saved) == 2


class _FakeResourceCollection:
    def __init__(self, raw: dict[str, Any] | None) -> None:
        self.raw = raw
        self.queries: list[dict[str, object]] = []

    async def find_one(self, query: dict[str, object]) -> dict[str, Any] | None:
        self.queries.append(query)
        return self.raw


class _FakeMongoClient:
    def __init__(self, databases: dict[str, dict[str, Any]]) -> None:
        self._databases = databases

    def __getitem__(self, database_name: str) -> dict[str, Any]:
        return self._databases[database_name]


class _FakeProjectionDatabase:
    def __init__(self, databases: dict[str, dict[str, Any]]) -> None:
        self.client = _FakeMongoClient(databases)


class _FakeProjectionCollection:
    def __init__(self, databases: dict[str, dict[str, Any]]) -> None:
        self.database = _FakeProjectionDatabase(databases)
        self.updates: list[tuple[dict[str, Any], dict[str, Any], bool]] = []

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool,
    ) -> None:
        self.updates.append((query, update, upsert))


@pytest.mark.asyncio
async def test_repository_upsert_is_guarded_by_acl_revision(monkeypatch) -> None:
    collection = _FakeProjectionCollection(databases={})
    monkeypatch.setattr(
        RagAclProjectionDocument,
        "get_pymongo_collection",
        classmethod(lambda cls: collection),
    )
    repository = MongoRagAclProjectionRepository(
        projector=RagAclProjector(),
        resource_database_name="wisepen_res_permission",
    )

    await repository.upsert_projection(
        RagResourceAclProjection(
            resource_id="res-1",
            acl_revision=200,
            owner_id="owner-1",
        )
    )

    query, update, upsert = collection.updates[0]
    assert query == {
        "resource_id": "res-1",
        "acl_revision": {"$lte": 200},
    }
    assert update["$set"]["acl_revision"] == 200
    assert upsert is True

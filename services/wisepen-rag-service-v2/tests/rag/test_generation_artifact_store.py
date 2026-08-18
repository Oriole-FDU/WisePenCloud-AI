from dataclasses import dataclass

import pytest

from rag.core.persistence.mongo import MongoGenerationArtifactStore
from rag.domain.entities import GenerationArtifactEntity


class _Field:
    def __init__(self, name: str) -> None:
        self.name = name

    def __eq__(self, other: object) -> tuple[str, object]:
        return self.name, other


@dataclass
class _Record:
    resource_id: str
    artifact_kind: str
    artifact_key: str
    payload: str


class _Query:
    def __init__(self, records: list[_Record]) -> None:
        self._records = records
        self.deleted = False

    async def to_list(self) -> list[_Record]:
        return self._records

    async def delete(self) -> None:
        self.deleted = True


class _Collection:
    def __init__(self) -> None:
        self.operations = []

    async def bulk_write(self, operations) -> None:
        self.operations = operations


@pytest.mark.asyncio
async def test_generation_artifacts_reads_only_matching_resource_kind_and_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        _Record("resource-1", "context", "key-1", "one"),
        _Record("resource-2", "context", "key-1", "other-resource"),
        _Record("resource-1", "graph", "key-1", "other-kind"),
    ]

    def find(*conditions):
        assert len(conditions) == 3
        assert conditions[0] == ("resource_id", "resource-1")
        assert conditions[1] == ("artifact_kind", "context")
        return _Query([records[0]])

    monkeypatch.setattr(
        GenerationArtifactEntity,
        "resource_id",
        _Field("resource_id"),
        raising=False,
    )
    monkeypatch.setattr(
        GenerationArtifactEntity,
        "artifact_kind",
        _Field("artifact_kind"),
        raising=False,
    )
    monkeypatch.setattr(
        GenerationArtifactEntity,
        "artifact_key",
        _Field("artifact_key"),
        raising=False,
    )
    monkeypatch.setattr(GenerationArtifactEntity, "find", find)

    result = await MongoGenerationArtifactStore().get_many(
        resource_id="resource-1",
        artifact_kind="context",
        artifact_keys=["key-1", "key-1", "missing"],
    )

    assert result == {"key-1": "one"}


@pytest.mark.asyncio
async def test_generation_artifacts_set_serializes_kind_and_overwrites_by_composite_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = _Collection()
    monkeypatch.setattr(
        GenerationArtifactEntity,
        "get_pymongo_collection",
        classmethod(lambda cls: collection),
    )

    await MongoGenerationArtifactStore().set_many(
        resource_id="resource-1",
        artifact_kind="graph",
        artifacts={"key-1": "new-value"},
    )

    operation = collection.operations[0]
    assert operation._filter == {
        "resource_id": "resource-1",
        "artifact_kind": "graph",
        "artifact_key": "key-1",
    }
    assert operation._doc["$set"] == {
        "resource_id": "resource-1",
        "artifact_kind": "graph",
        "artifact_key": "key-1",
        "payload": "new-value",
    }
    assert operation._upsert is True


@pytest.mark.asyncio
async def test_generation_artifacts_delete_deduplicates_resource_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = _Query([])
    monkeypatch.setattr(
        GenerationArtifactEntity,
        "resource_id",
        _Field("resource_id"),
        raising=False,
    )
    monkeypatch.setattr(GenerationArtifactEntity, "find", lambda *conditions: query)

    await MongoGenerationArtifactStore().delete_resources(
        ["resource-1", "resource-1", "resource-2"]
    )

    assert query.deleted is True


@pytest.mark.asyncio
async def test_generation_artifacts_ignores_empty_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def find(*conditions):
        nonlocal called
        called = True
        return _Query([])

    monkeypatch.setattr(GenerationArtifactEntity, "find", find)

    store = MongoGenerationArtifactStore()
    assert await store.get_many(
        resource_id="resource-1",
        artifact_kind="context",
        artifact_keys=[],
    ) == {}
    await store.delete_resources([])

    assert called is False

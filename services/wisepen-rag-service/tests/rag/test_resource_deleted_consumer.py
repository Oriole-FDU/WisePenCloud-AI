from __future__ import annotations

import pytest

from rag.application.rag.kafka_consumers import (
    RagResourceDeletedConsumer,
    RagResourceDeletionError,
)


class _DeletionTarget:
    def __init__(
        self,
        name: str,
        events: list[tuple[str, tuple[str, ...]]],
        *,
        fail_once: bool = False,
    ) -> None:
        self.name = name
        self.events = events
        self.fail_once = fail_once

    async def delete_resources(self, resource_ids: tuple[str, ...]) -> None:
        self.events.append((self.name, resource_ids))
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError(f"{self.name} unavailable")


@pytest.mark.parametrize(
    "payload",
    (
        {"typedResourceIds": []},
        {"typedResourceIds": {"DOCUMENT": "resource-1"}},
    ),
)
@pytest.mark.asyncio
async def test_consumer_rejects_malformed_java_payload(payload) -> None:
    consumer = RagResourceDeletedConsumer(targets=())

    with pytest.raises(RagResourceDeletionError):
        await consumer.handle(payload)


@pytest.mark.asyncio
async def test_consumer_deletes_each_projection_target_in_order() -> None:
    events: list[tuple[str, tuple[str, ...]]] = []
    consumer = RagResourceDeletedConsumer(
        targets=(
            _DeletionTarget("acl", events),
            _DeletionTarget("content", events),
            _DeletionTarget("qdrant", events),
            _DeletionTarget("neo4j", events),
        )
    )

    await consumer.handle(
        {
            "typedResourceIds": {
                "DOCUMENT": ["resource-1", "resource-2"],
                "NOTE": ["resource-2", "resource-3"],
            }
        }
    )

    assert events == [
        ("acl", ("resource-1", "resource-2", "resource-3")),
        ("content", ("resource-1", "resource-2", "resource-3")),
        ("qdrant", ("resource-1", "resource-2", "resource-3")),
        ("neo4j", ("resource-1", "resource-2", "resource-3")),
    ]


@pytest.mark.asyncio
async def test_consumer_replays_all_idempotent_targets_after_partial_failure() -> None:
    events: list[tuple[str, tuple[str, ...]]] = []
    consumer = RagResourceDeletedConsumer(
        targets=(
            _DeletionTarget("acl", events),
            _DeletionTarget("content", events),
            _DeletionTarget("qdrant", events, fail_once=True),
            _DeletionTarget("neo4j", events),
        )
    )
    payload = {"typedResourceIds": {"DOCUMENT": ["resource-1"]}}

    with pytest.raises(RuntimeError, match="qdrant unavailable"):
        await consumer.handle(payload)
    await consumer.handle(payload)

    assert [name for name, _ in events] == [
        "acl",
        "content",
        "qdrant",
        "acl",
        "content",
        "qdrant",
        "neo4j",
    ]

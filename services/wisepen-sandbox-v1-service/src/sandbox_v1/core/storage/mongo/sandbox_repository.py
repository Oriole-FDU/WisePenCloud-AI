from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Iterable

from common.core.exceptions import ServiceException

from sandbox_v1.core.observability.metrics import MetricsCollector
from sandbox_v1.core.storage.mongo.documents import (
    sandbox_record_from_doc,
    sandbox_record_to_doc,
    user_binding_to_doc,
)
from sandbox_v1.domain.entities import (
    PoolSnapshot,
    SandboxRecord,
    SandboxState,
    UserSandboxBindingRecord,
    can_transition,
    utc_now,
)
from sandbox_v1.domain.error_codes import SandboxErrorCode
from sandbox_v1.domain.interfaces.metrics import MetricsPort


class MongoSandboxRepository:
    """Mongo-backed authority for pool records and user bindings.

    Docker labels remain discovery hints. The authoritative READY/USER_ACTIVE
    state and user binding live in Mongo so service restart does not forget the
    current generation.
    """

    def __init__(
        self,
        *,
        database: Any,
        metrics: MetricsPort | None = None,
    ) -> None:
        self._database = database
        self._sandboxes = database["wisepen_sandbox_v1_sandbox"]
        self._bindings = database["wisepen_sandbox_v1_user_binding"]
        self._meta = database["wisepen_sandbox_v1_meta"]
        self._metrics = metrics or MetricsCollector()

    @property
    def metrics(self) -> MetricsPort:
        return self._metrics

    async def initialize(self) -> None:
        await self._database.command("ping")
        await self._sandboxes.create_index(
            [("sandbox_id", 1)],
            unique=True,
            name="uniq_sandbox_id",
        )
        await self._sandboxes.create_index(
            [("provider_id", 1)],
            name="idx_provider_id",
        )
        await self._sandboxes.create_index(
            [("state", 1), ("updated_at", 1)],
            name="idx_state_updated_at",
        )
        await self._bindings.create_index(
            [("user_id", 1)],
            unique=True,
            name="uniq_user_id",
        )
        await self._bindings.create_index(
            [("sandbox_id", 1)],
            unique=True,
            name="uniq_binding_sandbox_id",
        )
        await self._meta.update_one(
            {"_id": "pool"},
            {"$setOnInsert": {"generation": 0, "empty_checkouts": 0}},
            upsert=True,
        )

    async def save(self, record: SandboxRecord) -> None:
        await self._sandboxes.replace_one(
            {"sandbox_id": record.ref.sandbox_id},
            sandbox_record_to_doc(record),
            upsert=True,
        )
        await self._inc_generation()

    async def get(self, sandbox_id: str) -> SandboxRecord | None:
        doc = await self._sandboxes.find_one({"sandbox_id": sandbox_id})
        return sandbox_record_from_doc(doc) if doc is not None else None

    async def records_in(
        self,
        states: Iterable[SandboxState],
    ) -> list[SandboxRecord]:
        state_values = [state.value for state in states]
        cursor = self._sandboxes.find({"state": {"$in": state_values}})
        return [sandbox_record_from_doc(doc) async for doc in cursor]

    async def snapshot(
        self,
        *,
        min_ready: int = 0,
        target_ready: int = 0,
    ) -> PoolSnapshot:
        counts = {state: 0 for state in SandboxState}
        cursor = self._sandboxes.find({}, {"state": 1})
        async for doc in cursor:
            counts[SandboxState(doc["state"])] += 1

        meta = await self._meta.find_one({"_id": "pool"}) or {}
        ready = counts[SandboxState.READY]
        self._metrics.set_value(
            "active_user_bindings",
            counts[SandboxState.USER_ACTIVE],
        )
        return PoolSnapshot(
            generation=int(meta.get("generation") or 0),
            counts=counts,
            empty_checkouts=int(meta.get("empty_checkouts") or 0),
            metrics=self._metrics.snapshot(ready, min_ready, target_ready),
            min_ready=min_ready,
            target_ready=target_ready,
        )

    async def transition(
        self,
        sandbox_id: str,
        expected: SandboxState,
        state: SandboxState,
        *,
        error: str | None = None,
    ) -> SandboxRecord:
        if not can_transition(expected, state):
            raise ServiceException(
                SandboxErrorCode.INVALID_STATE_TRANSITION,
                f"cannot transition {expected.value} to {state.value}",
            )

        now = utc_now()
        updated = await self._sandboxes.find_one_and_update(
            {"sandbox_id": sandbox_id, "state": expected.value},
            {
                "$set": {
                    "state": state.value,
                    "updated_at": now,
                    "last_error": error,
                },
                "$inc": {"state_version": 1},
            },
            return_document=True,
        )
        if updated is None:
            current = await self._sandboxes.find_one({"sandbox_id": sandbox_id})
            if current is None:
                raise ServiceException(
                    SandboxErrorCode.SANDBOX_UNAVAILABLE,
                    f"sandbox {sandbox_id} does not exist",
                )
            raise ServiceException(
                SandboxErrorCode.INVALID_STATE_TRANSITION,
                f"cannot transition {current['state']} to {state.value}",
            )

        await self._inc_generation()
        return sandbox_record_from_doc(updated)

    async def checkout_ready(
        self,
        user_id: str,
        max_user_bindings: int = 20,
    ) -> SandboxRecord:
        binding = await self._bindings.find_one({"user_id": user_id})
        if binding is not None:
            return await self._reuse_binding(binding)

        if await self._bindings.count_documents({}) >= max_user_bindings:
            raise ServiceException(
                SandboxErrorCode.USER_SANDBOX_CAPACITY,
                "user sandbox capacity has been reached",
            )

        now = utc_now()
        binding_id = f"user_{uuid.uuid4().hex}"
        record_doc = await self._sandboxes.find_one_and_update(
            {"state": SandboxState.READY.value},
            {
                "$set": {
                    "state": SandboxState.USER_ACTIVE.value,
                    "owner_user_id": user_id,
                    "user_binding_id": binding_id,
                    "updated_at": now,
                    "last_error": None,
                },
                "$inc": {"state_version": 1},
            },
            sort=[("created_at", 1)],
            return_document=True,
        )
        if record_doc is None:
            await self._meta.update_one(
                {"_id": "pool"},
                {"$inc": {"empty_checkouts": 1}},
                upsert=True,
            )
            self._metrics.increment("pool_empty_checkouts")
            raise ServiceException(
                SandboxErrorCode.POOL_EMPTY,
                "sandbox pool has no READY container",
            )

        binding = UserSandboxBindingRecord(
            user_binding_id=binding_id,
            sandbox_id=record_doc["sandbox_id"],
            user_id=user_id,
            created_at=now,
            updated_at=now,
            last_active_at=now,
        )
        # Production runs a single sandbox service instance, but this upsert
        # still keeps repeated checkout requests from creating duplicate
        # bindings if the same user races within the process.
        await self._bindings.update_one(
            {"user_id": user_id},
            {"$setOnInsert": user_binding_to_doc(binding)},
            upsert=True,
        )
        self._metrics.increment("user_bindings_created")
        await self._inc_generation()
        return sandbox_record_from_doc(record_doc)

    async def records_older_than(
        self,
        state: SandboxState,
        cutoff: datetime,
    ) -> list[SandboxRecord]:
        cursor = self._sandboxes.find(
            {
                "state": state.value,
                "updated_at": {"$lte": cutoff},
            }
        )
        return [sandbox_record_from_doc(doc) async for doc in cursor]

    async def _reuse_binding(self, binding: dict[str, Any]) -> SandboxRecord:
        now = utc_now()
        updated_binding = await self._bindings.find_one_and_update(
            {"user_id": binding["user_id"]},
            {
                "$set": {
                    "updated_at": now,
                    "last_active_at": now,
                },
                "$inc": {"reuse_count": 1},
            },
            return_document=True,
        )
        record_doc = await self._sandboxes.find_one_and_update(
            {
                "sandbox_id": binding["sandbox_id"],
                "state": SandboxState.USER_ACTIVE.value,
            },
            {
                "$set": {
                    "updated_at": now,
                    "last_error": None,
                },
                "$inc": {"reuse_count": 1},
            },
            return_document=True,
        )
        if updated_binding is None or record_doc is None:
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "user container is not available",
            )

        self._metrics.increment("user_container_reuse_hits")
        await self._inc_generation()
        return sandbox_record_from_doc(record_doc)

    async def _inc_generation(self) -> None:
        await self._meta.update_one(
            {"_id": "pool"},
            {"$inc": {"generation": 1}},
            upsert=True,
        )

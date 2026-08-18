import json

import pytest

from rag.core.persistence.redis import RedisNavigationStateStore
from rag.domain.repositories.redis import NavigationStateMissingError


class _Redis:
    def __init__(self) -> None:
        self.hashes = {}
        self.expirations = []

    async def hset(self, key, mapping):
        self.hashes[key] = dict(mapping)

    async def expire(self, key, ttl):
        self.expirations.append((key, ttl))

    async def hgetall(self, key):
        return self.hashes.get(key, {})

    async def eval(self, script, numkeys, key, field, value, ttl):
        if key not in self.hashes:
            return None
        current = json.loads(self.hashes[key][field])
        additions = json.loads(value)
        added = []
        for node_id in additions:
            if node_id not in current:
                current.append(node_id)
                added.append(node_id)
        self.hashes[key][field] = json.dumps(current, ensure_ascii=False)
        self.expirations.append((key, ttl))
        return json.dumps(added)


def _store(redis: _Redis) -> RedisNavigationStateStore:
    return RedisNavigationStateStore(redis_client=redis, ttl_seconds=3600)


@pytest.mark.asyncio
async def test_state_persists_only_graph_navigation_anchors() -> None:
    redis = _Redis()
    store = _store(redis)
    state = await store.create(
        user_id="user-1",
        session_id="session-1",
        known_node_ids=["node-1", "node-1"],
    )

    key = next(iter(redis.hashes))
    assert set(redis.hashes[key]) == {
        "user_id",
        "session_id",
        "known_nodes",
    }
    loaded = await store.get(state.state_id)
    assert loaded is not None
    assert loaded.known_node_ids == ["node-1"]
    assert not hasattr(loaded, "known_sections")


@pytest.mark.asyncio
async def test_add_known_nodes_is_atomic_and_refreshes_ttl() -> None:
    redis = _Redis()
    store = _store(redis)
    state = await store.create(
        user_id="user-1",
        session_id="session-1",
        known_node_ids=[],
    )

    added = await store.add_known_nodes(
        state_id=state.state_id,
        node_ids=["node-2", "node-2"],
    )

    assert added == ["node-2"]
    assert (next(iter(redis.hashes)), 3600) == redis.expirations[-1]


@pytest.mark.asyncio
async def test_add_known_nodes_rejects_missing_state() -> None:
    with pytest.raises(NavigationStateMissingError):
        await _store(_Redis()).add_known_nodes(
            state_id="missing",
            node_ids=["node"],
        )

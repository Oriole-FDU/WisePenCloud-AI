from __future__ import annotations

import pytest

from chat.core.persistence.redis.knowledge_navigation_state_repository import (
    RedisKnowledgeNavigationStateRepository,
)


class _Redis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.expirations: dict[str, int] = {}
        self._commands: list[tuple] = []

    def pipeline(self, *, transaction: bool):
        assert transaction is True
        self._commands = []
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    def hset(self, key: str, *, mapping: dict[str, str]):
        self._commands.append(("hset", key, mapping))
        return self

    def sadd(self, key: str, *values: str):
        self._commands.append(("sadd", key, values))
        return self

    def expire(self, key: str, seconds: int):
        self._commands.append(("expire", key, seconds))
        return self

    async def execute(self):
        for command in self._commands:
            if command[0] == "hset":
                self.hashes[command[1]] = dict(command[2])
            elif command[0] == "sadd":
                self.sets.setdefault(command[1], set()).update(command[2])
            else:
                self.expirations[command[1]] = command[2]
        return []

    async def hgetall(self, key: str):
        return self.hashes.get(key, {})

    async def smembers(self, key: str):
        return self.sets.get(key, set())

    async def exists(self, key: str):
        return key in self.hashes


@pytest.mark.asyncio
async def test_navigation_state_round_trips_minimal_bound_state() -> None:
    redis = _Redis()
    repository = RedisKnowledgeNavigationStateRepository(
        redis_client=redis,
        ttl_seconds=1800,
    )

    created = await repository.create(
        user_id="user-1",
        session_id="session-1",
        root_query="概念之间有什么关系？",
        known_node_ids=("node-1", "node-1", "node-2"),
    )
    loaded = await repository.get(created.state_id)

    assert created.state_id.startswith("kns_")
    assert created.known_node_ids == ("node-1", "node-2")
    assert loaded == created
    state_key = f"wisepen:rag:navigation_state:{created.state_id}"
    known_nodes_key = f"{state_key}:known_nodes"
    assert redis.expirations[state_key] == 1800
    assert redis.expirations[known_nodes_key] == 1800

    updated = await repository.add_known_nodes(
        state_id=created.state_id,
        node_ids=("node-2", "node-3"),
    )
    loaded = await repository.get(created.state_id)
    assert updated is True
    assert loaded is not None
    assert loaded.known_node_ids == ("node-1", "node-2", "node-3")


"""Redis 导航状态 adapter，所有状态字段共用一个 hash key。"""

import json
from collections.abc import Mapping, Sequence
from uuid import uuid4

from redis.asyncio import Redis

from rag.domain.repositories.redis.navigation_state_store import (
    NavigationState,
    NavigationStateMissingError,
    NavigationStateStore,
)

_KEY_PREFIX = "wisepen:rag:v2:navigation-state:"
_NODES_FIELD = "known_nodes"

_ADD_NODES_SCRIPT = """
if redis.call('EXISTS', KEYS[1]) == 0 then
    return nil
end
local current = redis.call('HGET', KEYS[1], ARGV[1])
local nodes = cjson.decode(current or '[]')
local seen = {}
for _, node_id in ipairs(nodes) do
    seen[node_id] = true
end
local additions = cjson.decode(ARGV[2])
local added = {}
for _, node_id in ipairs(additions) do
    if not seen[node_id] then
        table.insert(nodes, node_id)
        table.insert(added, node_id)
        seen[node_id] = true
    end
end
redis.call('HSET', KEYS[1], ARGV[1], cjson.encode(nodes))
redis.call('EXPIRE', KEYS[1], ARGV[3])
return cjson.encode(added)
"""


class RedisNavigationStateStore(NavigationStateStore):
    """以单 hash key 保存状态，并统一续期主 key。"""

    def __init__(self, *, redis_client: Redis, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds

    async def create(
        self,
        *,
        user_id: str,
        session_id: str,
        known_node_ids: Sequence[str],
    ) -> NavigationState:
        state = NavigationState(
            state_id=f"nav_{uuid4().hex}",
            user_id=user_id,
            session_id=session_id,
            known_node_ids=list(dict.fromkeys(known_node_ids)),
        )
        key = self._key(state.state_id)
        await self._redis.hset(key, mapping=_to_hash(state))
        await self._redis.expire(key, self._ttl_seconds)
        return state

    async def get(self, state_id: str) -> NavigationState | None:
        values = await self._redis.hgetall(self._key(state_id))
        if not values:
            return None
        return _to_navigation_state(state_id, values)

    async def add_known_nodes(
        self,
        *,
        state_id: str,
        node_ids: Sequence[str],
    ) -> list[str]:
        result = await self._redis.eval(
            _ADD_NODES_SCRIPT,
            1,
            self._key(state_id),
            _NODES_FIELD,
            json.dumps(list(dict.fromkeys(node_ids)), ensure_ascii=False),
            self._ttl_seconds,
        )
        if result is None:
            raise NavigationStateMissingError(state_id)
        value = result.decode() if isinstance(result, bytes) else result
        added = json.loads(value)
        if not isinstance(added, list) or not all(
            isinstance(node_id, str) for node_id in added
        ):
            raise TypeError(f"navigation state {state_id} returned invalid node IDs")
        return added

    @staticmethod
    def _key(state_id: str) -> str:
        return f"{_KEY_PREFIX}{state_id}"


def _to_hash(state: NavigationState) -> dict[str, str]:
    return {
        "user_id": state.user_id,
        "session_id": state.session_id,
        "known_nodes": json.dumps(state.known_node_ids, ensure_ascii=False),
    }


def _to_navigation_state(
    state_id: str,
    values: Mapping[object, object],
) -> NavigationState:
    nodes_value = json.loads(_read_text(values, "known_nodes"))
    if not isinstance(nodes_value, list):
        raise TypeError(f"navigation state {state_id} has invalid collection fields")

    if not all(isinstance(node_id, str) for node_id in nodes_value):
        raise TypeError(f"navigation state {state_id} has invalid node IDs")

    return NavigationState(
        state_id=state_id,
        user_id=_read_text(values, "user_id"),
        session_id=_read_text(values, "session_id"),
        known_node_ids=list(dict.fromkeys(nodes_value)),
    )


def _read_text(values: Mapping[object, object], field_name: str) -> str:
    value = values.get(field_name)
    if value is None:
        value = values.get(field_name.encode())
    if not isinstance(value, (str, bytes)):
        raise TypeError(f"navigation state field {field_name} is invalid")
    return value.decode() if isinstance(value, bytes) else value

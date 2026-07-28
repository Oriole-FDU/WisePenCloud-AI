from __future__ import annotations

import uuid

from redis.asyncio import Redis

from chat.application.rag.knowledge_navigation import KnowledgeNavigationState
from chat.application.rag.repositories import KnowledgeNavigationStateRepository
from chat.core.persistence.redis.base import RedisRepository

_KEY_PREFIX = "wisepen:rag:navigation_state:"
_KNOWN_NODES_SUFFIX = ":known_nodes"


class RedisKnowledgeNavigationStateRepository(
    RedisRepository,
    KnowledgeNavigationStateRepository,
):
    """保存与用户、会话绑定的最小知识导航状态。"""

    __slots__ = ("_ttl_seconds",)

    def __init__(self, *, redis_client: Redis, ttl_seconds: int) -> None:
        super().__init__(redis_client=redis_client)
        self._ttl_seconds = ttl_seconds

    async def create(
        self,
        *,
        user_id: str,
        session_id: str,
        root_query: str,
        known_node_ids: tuple[str, ...] = (),
    ) -> KnowledgeNavigationState:
        state = KnowledgeNavigationState(
            state_id=f"kns_{uuid.uuid4().hex}",
            user_id=user_id,
            session_id=session_id,
            root_query=root_query,
            known_node_ids=tuple(dict.fromkeys(known_node_ids)),
        )
        state_key = f"{_KEY_PREFIX}{state.state_id}"
        known_nodes_key = f"{state_key}{_KNOWN_NODES_SUFFIX}"
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.hset(
                state_key,
                mapping={
                    "user_id": state.user_id,
                    "session_id": state.session_id,
                    "root_query": state.root_query,
                },
            ).expire(state_key, self._ttl_seconds)
            if state.known_node_ids:
                pipe.sadd(known_nodes_key, *state.known_node_ids).expire(
                    known_nodes_key,
                    self._ttl_seconds,
                )
            await pipe.execute()
        return state

    async def get(self, state_id: str) -> KnowledgeNavigationState | None:
        state_key = f"{_KEY_PREFIX}{state_id}"
        values = await self._redis.hgetall(state_key)
        if not values:
            return None
        known_node_ids = await self._redis.smembers(f"{state_key}{_KNOWN_NODES_SUFFIX}")
        return KnowledgeNavigationState(
            state_id=state_id,
            user_id=values["user_id"],
            session_id=values["session_id"],
            root_query=values["root_query"],
            known_node_ids=tuple(sorted(known_node_ids)),
        )

    async def add_known_nodes(
        self,
        *,
        state_id: str,
        node_ids: tuple[str, ...],
    ) -> bool:
        state_key = f"{_KEY_PREFIX}{state_id}"
        if not await self._redis.exists(state_key):
            return False
        known_nodes_key = f"{state_key}{_KNOWN_NODES_SUFFIX}"
        unique_node_ids = tuple(dict.fromkeys(node_ids))
        async with self._redis.pipeline(transaction=True) as pipe:
            if unique_node_ids:
                pipe.sadd(known_nodes_key, *unique_node_ids).expire(
                    known_nodes_key,
                    self._ttl_seconds,
                )
            pipe.expire(state_key, self._ttl_seconds)
            await pipe.execute()
        return True

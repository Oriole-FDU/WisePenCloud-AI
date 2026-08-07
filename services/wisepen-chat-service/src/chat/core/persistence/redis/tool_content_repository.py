from __future__ import annotations

from chat.application.tools.common.tool_content_store import StoredToolContent
from chat.core.persistence.redis.base import RedisRepository
from chat.domain.repositories import ToolContentRepository
from pydantic import TypeAdapter
from redis.asyncio import Redis

_CONTENT_KEY_PREFIX = "wisepen:tool_content:v2:item:"
_SESSION_KEY_PREFIX = "wisepen:tool_content:v2:session:"
_STORED_CONTENT_ADAPTER = TypeAdapter(StoredToolContent)


class RedisToolContentRepository(RedisRepository, ToolContentRepository):
    """基于 Redis 的短期工具内容仓储。"""

    __slots__ = ("_ttl_seconds",)

    def __init__(self, *, redis_client: Redis, ttl_seconds: int) -> None:
        super().__init__(redis_client=redis_client)
        self._ttl_seconds = ttl_seconds

    async def put(self, stored: StoredToolContent) -> None:
        content_key = f"{_CONTENT_KEY_PREFIX}{stored.content_id}"
        session_key = f"{_SESSION_KEY_PREFIX}{stored.session_id}"

        # 原子写入内容，并将 content_id 记录到当前会话索引
        async with self._redis.pipeline(transaction=True) as pipe:
            await (
                pipe.set(
                    content_key,
                    _STORED_CONTENT_ADAPTER.dump_json(stored),
                    ex=self._ttl_seconds,
                )
                .sadd(session_key, stored.content_id)
                .expire(session_key, self._ttl_seconds)
                .execute()
            )

    async def get(self, content_id: str) -> StoredToolContent | None:
        raw = await self._redis.get(
            f"{_CONTENT_KEY_PREFIX}{content_id}"
        )
        return (
            _STORED_CONTENT_ADAPTER.validate_json(raw)
            if raw is not None
            else None
        )

from __future__ import annotations

from redis.asyncio import Redis

from chat.application.tools.core.output_cache.cache_store import StoredToolContent
from chat.core.config.app_settings import settings
from chat.core.persistence.redis.base import RedisRepository
from chat.domain.repositories import ToolContentRepository
from pydantic import TypeAdapter

_CONTENT_KEY_PREFIX = "wisepen:tool_content:v1:item:"
_SESSION_KEY_PREFIX = "wisepen:tool_content:v1:session:"
_STORED_CONTENT_ADAPTER = TypeAdapter(StoredToolContent)


class RedisToolContentRepository(RedisRepository, ToolContentRepository):
    """基于 Redis 的短期工具内容仓储"""

    def __init__(self, *, redis_client: Redis) -> None:
        super().__init__(redis_client=redis_client)
        self.ttl = settings.tools.content.default_ttl_seconds

    async def put(self, stored: StoredToolContent) -> None:
        content_key = f"{_CONTENT_KEY_PREFIX}{stored.content_id}"
        session_key = f"{_SESSION_KEY_PREFIX}{stored.session_id}"

        # 原子写入内容，并将 content_id 记录到当前会话索引
        async with self._redis.pipeline(transaction=True) as pipe:
            await (
                pipe.set(
                    content_key,
                    _STORED_CONTENT_ADAPTER.dump_json(stored),
                    ex=self.ttl,
                )
                .sadd(session_key, stored.content_id)
                .expire(session_key, self.ttl)
                .execute()
            )

    async def get(self, content_id: str) -> StoredToolContent | None:
        raw = await self._redis.get(f"{_CONTENT_KEY_PREFIX}{content_id}")
        return (
            _STORED_CONTENT_ADAPTER.validate_json(raw)
            if raw is not None
            else None
        )

from __future__ import annotations

import redis.asyncio as redis
from pydantic import TypeAdapter

from chat.application.tools.core.output_cache.cache_store import StoredToolContent
from chat.core.config.app_settings import settings
from chat.domain.repositories import ToolContentRepository

_CONTENT_KEY_PREFIX = "wisepen:tool_content:v5:item:"
_SESSION_KEY_PREFIX = "wisepen:tool_content:v5:session:"
_STORED_CONTENT_ADAPTER = TypeAdapter(StoredToolContent)


class RedisToolContentRepository(ToolContentRepository):
    """自行创建 Redis 连接和 TTL 的工具正文仓储。"""

    def __init__(self) -> None:
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=False)
        self.ttl = settings.TOOL_CONTENT_DEFAULT_TTL_SECONDS

    async def put(self, stored: StoredToolContent) -> None:
        content_key = f"{_CONTENT_KEY_PREFIX}{stored.content_id}"
        session_key = f"{_SESSION_KEY_PREFIX}{stored.session_id}"

        # 原子写入内容，并将 content_id 记录到当前会话索引。
        async with self.redis.pipeline(transaction=True) as pipe:
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
        raw = await self.redis.get(f"{_CONTENT_KEY_PREFIX}{content_id}")
        return (
            _STORED_CONTENT_ADAPTER.validate_json(raw)
            if raw is not None
            else None
        )

from __future__ import annotations

from redis.asyncio import Redis

from chat.application.tools.common.tool_content_store.core.models import (
    StoredToolContent,
)
from chat.application.tools.common.tool_content_store.core.repository_protocol import (
    ToolContentRepository,
)
from chat.core.persistence.redis._utils.cache_codec import dumps_cache, loads_cache_or_none
from chat.core.persistence.redis.base import RedisRepository

# --- 全局命名空间配置 ---
_CONTENT_KEY_PREFIX = "wisepen:tool_content:item:"
_SESSION_KEY_PREFIX = "wisepen:tool_content:session:"


class RedisToolContentRepository(RedisRepository, ToolContentRepository):
    """基于 Redis 的 ToolContent 仓储实现。"""

    __slots__ = ("_ttl_seconds",)

    def __init__(self, *, redis_client: Redis, ttl_seconds: int) -> None:
        super().__init__(redis_client=redis_client)
        self._ttl_seconds = ttl_seconds

    async def put(self, stored: StoredToolContent) -> None:
        """写入完整 ToolContent，并维护会话级 content_id 集合。"""
        item_key = self._item_key(stored.content_id)
        session_key = self._session_key(stored.session_id)

        # 开启事务管线，保证单体 KV 和 Session 集合同时成功并保持生命周期一致
        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.set(item_key, dumps_cache(stored), ex=self._ttl_seconds)
            await pipe.sadd(session_key, stored.content_id)
            await pipe.expire(session_key, self._ttl_seconds)
            await pipe.execute()

    async def get(self, content_id: str) -> StoredToolContent | None:
        """按 content_id 读取并反序列化 ToolContent。"""
        raw = await self._redis.get(self._item_key(content_id))
        if raw is None:
            return None

        return loads_cache_or_none(raw, StoredToolContent)

    @staticmethod
    def _item_key(content_id: str) -> str:
        return f"{_CONTENT_KEY_PREFIX}{content_id}"

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"{_SESSION_KEY_PREFIX}{session_id}"

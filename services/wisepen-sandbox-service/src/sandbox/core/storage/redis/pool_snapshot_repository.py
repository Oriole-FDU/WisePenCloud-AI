from __future__ import annotations

import json
import redis.asyncio as redis

from common.logger import warn
from sandbox.core.config.app_settings import settings
from sandbox.domain.entities import PoolSnapshot
from sandbox.domain.repositories.pool_snapshot_repository import PoolSnapshotRepository


class RedisPoolSnapshotRepository(PoolSnapshotRepository):
    """PoolSnapshot 的 Redis 仓储实现。"""

    def __init__(self) -> None:
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)

    def _get_key(self) -> str:
        return f"wisepen:sandbox:pool_snapshot"

    def _serialize(self, snapshot: PoolSnapshot) -> str:
        return json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False)

    def _deserialize(self, value: str) -> PoolSnapshot:
        payload = json.loads(value)
        return PoolSnapshot.model_validate(payload)

    async def save(self, snapshot: PoolSnapshot) -> None:
        key = self._get_key()
        try:
            await self.redis.set(key, self._serialize(snapshot))
        except Exception as e:
            warn("set sandbox pool snapshot failed.", key=key, exc=e)

    async def get(self) -> PoolSnapshot | None:
        key = self._get_key()
        try:
            value = await self.redis.get(key)
            if value is None:
                return None
            return self._deserialize(str(value))
        except Exception as e:
            warn("get sandbox pool snapshot failed.", key=key, exc=e)
            return None

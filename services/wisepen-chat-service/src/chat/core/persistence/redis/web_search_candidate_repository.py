from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import redis.asyncio as redis

from chat.application.tools.web_tools.web_search.candidate_store.models import (
    WebSearchCandidateMapping,
)
from chat.application.tools.web_tools.web_search.candidate_store.repository import (
    WebSearchCandidateRepository,
)

# --- 全局配置 ---
_KEY_PREFIX = "wisepen:web_search_candidate:"


class RedisWebSearchCandidateRepository(WebSearchCandidateRepository):
    """Redis 实现：保存 web_search 候选 ID 到 URL 的短期映射。"""

    __slots__ = ("_redis",)
    _redis: redis.Redis  # __slots__ 不影响类型标注，仅用于静态检查

    def __init__(self, *, redis_url: str) -> None:
        self._redis = redis.from_url(redis_url, decode_responses=True)

    async def set_mapping(
            self,
            mapping: WebSearchCandidateMapping,
            *,
            ttl_seconds: int,
    ) -> None:
        key = self._key(mapping.user_id, mapping.search_ref)

        # 将 DataClass 转换为字典并序列化为非 ASCII 转义的标准 JSON
        payload = json.dumps(asdict(mapping), ensure_ascii=False)

        await self._redis.set(key, payload, ex=ttl_seconds)

    async def get_mapping(
            self,
            *,
            user_id: str,
            search_ref: str,
    ) -> WebSearchCandidateMapping | None:
        raw = await self._redis.get(self._key(user_id, search_ref))
        if raw is None:
            return None

        # 内联反序列化解析 (Inline Decoding)
        payload: dict[str, Any] = json.loads(raw)
        metadata = payload.get("metadata")

        return WebSearchCandidateMapping(
            user_id=str(payload["user_id"]),
            search_ref=str(payload["search_ref"]),
            search_run_id=str(payload["search_run_id"]),
            candidate_id=str(payload["candidate_id"]),
            source_id=str(payload["source_id"]),
            url=str(payload["url"]),
            source_scope=str(payload["source_scope"]),
            metadata=metadata if isinstance(metadata, dict) else {},
        )

    async def delete_mapping(
            self,
            *,
            user_id: str,
            search_ref: str,
    ) -> None:
        await self._redis.delete(self._key(user_id, search_ref))

    @staticmethod
    def _key(*parts: str) -> str:
        """拼接 Redis key。

        每个片段单独进行十六进制编码（hex），彻底避免用户传入的 ID 包含
        冒号（:）或方括号（[]）等特殊字符从而污染或破坏 Redis 的命名空间层级。
        """
        encoded_parts = ":".join(p.encode("utf-8").hex() for p in parts)
        return f"{_KEY_PREFIX}{encoded_parts}"
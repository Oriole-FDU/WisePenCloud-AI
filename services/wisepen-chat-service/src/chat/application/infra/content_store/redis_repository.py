import json
from dataclasses import asdict
from typing import Any, Dict, List, Optional

import redis

from chat.application.infra.content_store.models import ContentChunk, StoredContent

_CONTENT_KEY_PREFIX = "wisepen:tool_content:item:"
_SCOPE_KEY_PREFIX = "wisepen:tool_content:scope:"


class RedisContentRepository:
    """基于 Redis 的 ToolContent 内容仓储。"""

    def __init__(self, *, redis_url: str, ttl_seconds: int) -> None:
        """初始化 Redis 内容仓储。

        Args:
            redis_url: Redis 连接地址。
            ttl_seconds: 缓存 key 的存活时间，单位秒。
        """
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._ttl_seconds = ttl_seconds

    def put(self, content: StoredContent) -> None:
        """写入内容，并维护会话作用域下的 content_id 集合。"""
        item_key = self._item_key(content.content_id)
        scope_key = self._scope_key(content.scope_id)
        payload = json.dumps(asdict(content), ensure_ascii=False)

        pipe = self._redis.pipeline(transaction=True)
        pipe.set(item_key, payload, ex=self._ttl_seconds)
        pipe.sadd(scope_key, content.content_id)
        pipe.expire(scope_key, self._ttl_seconds)
        pipe.execute()

    def get(self, content_id: str) -> Optional[StoredContent]:
        """按 content_id 读取内容；不存在或过期时返回 None。"""
        raw = self._redis.get(self._item_key(content_id))
        if raw is None:
            return None

        payload = json.loads(raw)
        return self._deserialize(payload)

    def delete(self, content_id: str) -> None:
        """按 content_id 删除内容。"""
        item_key = self._item_key(content_id)
        raw = self._redis.get(item_key)
        if raw is None:
            return

        payload = json.loads(raw)
        scope_id = str(payload["scope_id"])
        scope_key = self._scope_key(scope_id)

        pipe = self._redis.pipeline(transaction=True)
        pipe.delete(item_key)
        pipe.srem(scope_key, content_id)
        pipe.execute()

    @staticmethod
    def _item_key(content_id: str) -> str:
        return f"{_CONTENT_KEY_PREFIX}{content_id}"

    @staticmethod
    def _scope_key(scope_id: str) -> str:
        return f"{_SCOPE_KEY_PREFIX}{scope_id}"

    @staticmethod
    def _deserialize(payload: Dict[str, Any]) -> StoredContent:
        chunks: List[ContentChunk] = [
            ContentChunk(
                index=int(chunk["index"]),
                start_offset=int(chunk["start_offset"]),
                end_offset=int(chunk["end_offset"]),
                token_count=chunk.get("token_count"),
                metadata=chunk.get("metadata") or {},
            )
            for chunk in payload.get("chunks", [])
        ]
        return StoredContent(
            content_id=str(payload["content_id"]),
            scope_id=str(payload["scope_id"]),
            producer=str(payload["producer"]),
            source=str(payload["source"]),
            content_type=str(payload["content_type"]),
            text=str(payload["text"]),
            chunks=chunks,
            metadata=payload.get("metadata") or {},
        )
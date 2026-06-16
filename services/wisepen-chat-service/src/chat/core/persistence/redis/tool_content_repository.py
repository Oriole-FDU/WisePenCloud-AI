from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import redis.asyncio as redis

from chat.application.tools.common.tool_content_store import ToolContentRepository
from chat.application.tools.common.tool_content_store.models import (
    StoredToolContent,
    ToolContentChunk,
    ToolContentIndex,
    ToolContentIndexEntry,
)

_CONTENT_KEY_PREFIX = "wisepen:tool_content:item:"
_SESSION_KEY_PREFIX = "wisepen:tool_content:session:"


class RedisToolContentRepository(ToolContentRepository):
    """基于 Redis 的 ToolContent 仓储实现。"""

    __slots__ = ("_redis", "_ttl_seconds")

    def __init__(self, *, redis_url: str, ttl_seconds: int) -> None:
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._ttl_seconds = ttl_seconds

    async def put(self, stored: StoredToolContent) -> None:
        """写入完整 ToolContent，并维护会话级 content_id 集合。"""
        item_key = self._item_key(stored.content_id)
        session_key = self._session_key(stored.session_id)
        payload = json.dumps(_jsonable(asdict(stored)), ensure_ascii=False)

        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.set(item_key, payload, ex=self._ttl_seconds)
            await pipe.sadd(session_key, stored.content_id)
            await pipe.expire(session_key, self._ttl_seconds)
            await pipe.execute()

    async def get(self, content_id: str) -> StoredToolContent | None:
        """按 content_id 读取并反序列化 ToolContent。"""
        raw = await self._redis.get(self._item_key(content_id))
        if raw is None:
            return None
        return _decode_stored(json.loads(raw))

    @staticmethod
    def _item_key(content_id: str) -> str:
        return f"{_CONTENT_KEY_PREFIX}{content_id}"

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"{_SESSION_KEY_PREFIX}{session_id}"


def _jsonable(value: Any) -> Any:
    """递归转换为 JSON 可序列化值。"""
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def _decode_stored(payload: dict[str, Any]) -> StoredToolContent:
    """将 Redis JSON 载荷反序列化为 StoredToolContent。"""
    chunks = tuple(
        ToolContentChunk(
            chunk_index=int(chunk["chunk_index"]),
            start_offset=chunk.get("start_offset"),
            end_offset=chunk.get("end_offset"),
            unit_types=tuple(str(value) for value in chunk.get("unit_types", [])),
            section_path=tuple(str(value) for value in chunk.get("section_path", [])),
            anchor_names=tuple(str(value) for value in chunk.get("anchor_names", [])),
        )
        for chunk in payload.get("chunks", [])
    )
    index_payload = payload.get("index") or {}
    entries = tuple(
        ToolContentIndexEntry(
            name=str(entry["name"]),
            chunk_indices=tuple(int(index) for index in entry.get("chunk_indices", [])),
        )
        for entry in index_payload.get("entries", [])
    )

    return StoredToolContent(
        content_id=str(payload["content_id"]),
        session_id=str(payload["session_id"]),
        producer=str(payload["producer"]),
        source=str(payload["source"]),
        content_type=str(payload["content_type"]),
        content_role=str(payload["content_role"]),
        text=str(payload["text"]),
        chunks=chunks,
        index=ToolContentIndex(entries=entries),
        metadata=payload.get("metadata") or {},
    )

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Protocol

import redis

from chat.application.tools.common.chunking_engine import ChunkLevel, IndexKind

from .models import (
    StoredToolContent,
    ToolContentChunk,
    ToolContentIndex,
    ToolContentIndexEntry,
)


_CONTENT_KEY_PREFIX = "wisepen:tool_content:item:"
_SESSION_KEY_PREFIX = "wisepen:tool_content:session:"


class ToolContentRepository(Protocol):
    """ToolContent 持久化仓储协议。"""

    def put(self, stored: StoredToolContent) -> None:
        """写入 ToolContent。"""
        ...

    def get(self, content_id: str) -> StoredToolContent | None:
        """按 content_id 读取 ToolContent。"""
        ...


class RedisToolContentRepository:
    """基于 Redis 的 ToolContent 仓储。"""

    __slots__ = ("_redis", "_ttl_seconds")

    def __init__(self, *, redis_url: str, ttl_seconds: int) -> None:
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._ttl_seconds = ttl_seconds

    def put(self, stored: StoredToolContent) -> None:
        item_key = self._item_key(stored.content_id)
        session_key = self._session_key(stored.session_id)
        payload = json.dumps(_jsonable(asdict(stored)), ensure_ascii=False)

        pipe = self._redis.pipeline(transaction=True)
        pipe.set(item_key, payload, ex=self._ttl_seconds)
        pipe.sadd(session_key, stored.content_id)
        pipe.expire(session_key, self._ttl_seconds)
        pipe.execute()

    def get(self, content_id: str) -> StoredToolContent | None:
        raw = self._redis.get(self._item_key(content_id))
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
    chunks = tuple(
        ToolContentChunk(
            chunk_id=str(chunk["chunk_id"]),
            chunk_index=int(chunk["chunk_index"]),
            level=ChunkLevel(str(chunk["level"])),
            parent_chunk_id=chunk.get("parent_chunk_id"),
            start_offset=chunk.get("start_offset"),
            end_offset=chunk.get("end_offset"),
            start_unit=chunk.get("start_unit"),
            end_unit=chunk.get("end_unit"),
            content_hash=str(chunk.get("content_hash") or ""),
            unit_types=tuple(str(value) for value in chunk.get("unit_types", [])),
            section_path=tuple(str(value) for value in chunk.get("section_path", [])),
            anchor_names=tuple(str(value) for value in chunk.get("anchor_names", [])),
            page_name=chunk.get("page_name"),
            metadata=chunk.get("metadata") or {},
        )
        for chunk in payload.get("chunks", [])
    )
    index_payload = payload.get("index") or {}
    entries = tuple(
        ToolContentIndexEntry(
            name=str(entry["name"]),
            kind=IndexKind(str(entry["kind"])),
            chunk_indices=tuple(int(index) for index in entry.get("chunk_indices", [])),
            chunk_ids=tuple(str(chunk_id) for chunk_id in entry.get("chunk_ids", [])),
            start_offset=entry.get("start_offset"),
            end_offset=entry.get("end_offset"),
            metadata=entry.get("metadata") or {},
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

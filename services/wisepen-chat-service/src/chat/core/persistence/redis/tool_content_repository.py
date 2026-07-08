from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from chat.application.tools.common.tool_content_store.core.models import (
    StoredToolContent,
    ToolContentChunk,
    ToolContentIndex,
    ToolContentIndexEntry,
)
from chat.application.tools.common.tool_content_store.core.repository_protocol import (
    ToolContentRepository,
)
from chat.core.persistence.redis._utils.jsonable import to_jsonable
from chat.core.persistence.redis.base import RedisRepository

# --- 全局命名空间配置 ---
_CONTENT_KEY_PREFIX = "wisepen:tool_content:item:"
_SESSION_KEY_PREFIX = "wisepen:tool_content:session:"


class RedisToolContentRepository(RedisRepository, ToolContentRepository):
    """基于 Redis 的 ToolContent 仓储实现。"""

    __slots__ = ("_ttl_seconds",)

    def __init__(self, *, redis_url: str, ttl_seconds: int) -> None:
        super().__init__(redis_url=redis_url)
        self._ttl_seconds = ttl_seconds

    async def put(self, stored: StoredToolContent) -> None:
        """写入完整 ToolContent，并维护会话级 content_id 集合。"""
        item_key = self._item_key(stored.content_id)
        session_key = self._session_key(stored.session_id)

        # 将结构化的 StoredToolContent 拍平为可序列化的 JSON 载荷
        payload = json.dumps(to_jsonable(asdict(stored)), ensure_ascii=False)

        # 开启事务管线，保证单体 KV 和 Session 集合同时成功并保持生命周期一致
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

        # 内联反序列化解析 (Inline Decoding)
        payload: dict[str, Any] = json.loads(raw)

        # 1. 解析 chunks 嵌套元组
        chunks = tuple(
            ToolContentChunk(
                chunk_index=int(chunk["chunk_index"]),
                start_offset=chunk.get("start_offset"),
                end_offset=chunk.get("end_offset"),
                block_kinds=tuple(str(v) for v in chunk.get("block_kinds", [])),
                section_path=tuple(str(v) for v in chunk.get("section_path", [])),
                page_label=(
                    str(chunk["page_label"]).strip()
                    if chunk.get("page_label") is not None
                    else None
                ),
                anchor_labels=tuple(str(v) for v in chunk.get("anchor_labels", [])),
            )
            for chunk in payload.get("chunks", [])
        )

        # 2. 解析索引结构 entries 嵌套元组
        index_payload: dict[str, Any] = payload.get("index") or {}
        entries = tuple(
            ToolContentIndexEntry(
                locator_name=str(entry["locator_name"]),
                locator_kind=str(entry["locator_kind"]),
                chunk_indices=tuple(int(idx) for idx in entry.get("chunk_indices", [])),
                start_offset=entry.get("start_offset"),
                end_offset=entry.get("end_offset"),
                section_path=tuple(str(v) for v in entry.get("section_path", [])),
                page_label=(
                    str(entry["page_label"]).strip()
                    if entry.get("page_label") is not None
                    else None
                ),
                anchor_label=(
                    str(entry["anchor_label"]).strip()
                    if entry.get("anchor_label") is not None
                    else None
                ),
            )
            for entry in index_payload.get("entries", [])
        )

        # 3. 完整拼装领域实体模型
        return StoredToolContent(
            content_id=str(payload["content_id"]),
            session_id=str(payload["session_id"]),
            content_type=str(payload["content_type"]),
            text=str(payload["text"]),
            chunks=chunks,
            index=ToolContentIndex(entries=entries),
            metadata=payload.get("metadata") or {},
        )

    @staticmethod
    def _item_key(content_id: str) -> str:
        return f"{_CONTENT_KEY_PREFIX}{content_id}"

    @staticmethod
    def _session_key(session_id: str) -> str:
        return f"{_SESSION_KEY_PREFIX}{session_id}"

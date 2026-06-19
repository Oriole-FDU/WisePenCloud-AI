from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from typing import Any

import redis.asyncio as redis

from chat.application.tools.common.tool_run_file_store.models import ToolFileRefRecord
from chat.application.tools.common.tool_run_file_store.store import ToolRunFileRepository

# --- 全局命名空间配置 ---
_REF_KEY_PREFIX = "wisepen:tool_file_ref:item:"
_SESSION_KEY_PREFIX = "wisepen:tool_file_ref:session:"


class RedisToolRunFileRepository(ToolRunFileRepository):
    """Redis 元数据仓库，用于 `tfile_*` 引用。"""

    __slots__ = ("_redis",)

    def __init__(self, *, redis_url: str) -> None:
        self._redis = redis.from_url(redis_url, decode_responses=True)

    async def put(self, record: ToolFileRefRecord, *, ttl_seconds: int) -> None:
        """写入文件引用元数据。"""
        item_key = self._item_key(record.ref_id)
        session_key = self._session_key(
            user_id=record.user_id,
            session_id=record.session_id,
        )

        # 将 DataClass 预转为兼容日期等特殊类型的可序列化载荷
        payload = json.dumps(_jsonable(asdict(record)), ensure_ascii=False)

        # 使用 Pipeline (transaction=True) 保证单体 KV 记录与会话集合添加的原子性
        async with self._redis.pipeline(transaction=True) as pipe:
            await pipe.set(item_key, payload, ex=ttl_seconds)
            await pipe.sadd(session_key, record.ref_id)
            await pipe.expire(session_key, ttl_seconds)
            await pipe.execute()

    async def get(self, ref_id: str) -> ToolFileRefRecord | None:
        """按 tfile_* 引用读取文件引用元数据。"""
        raw = await self._redis.get(self._item_key(ref_id))
        if raw is None:
            return None

        # 内联反序列化解析 (Inline Decoding)
        payload: dict[str, Any] = json.loads(raw)

        return ToolFileRefRecord(
            ref_id=str(payload["ref_id"]),
            user_id=str(payload["user_id"]),
            session_id=str(payload["session_id"]),
            producer=str(payload["producer"]),
            sha256=str(payload["sha256"]),
            object_rel_path=str(payload["object_rel_path"]),
            filename=str(payload["filename"]),
            content_type=(
                str(payload["content_type"])
                if payload.get("content_type") is not None
                else None
            ),
            size_bytes=int(payload["size_bytes"]),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
            expires_at=datetime.fromisoformat(str(payload["expires_at"])),
            metadata=payload.get("metadata") or {},
        )

    async def delete(self, ref_id: str) -> None:
        """删除文件引用元数据。"""
        await self._redis.delete(self._item_key(ref_id))

    @staticmethod
    def _item_key(ref_id: str) -> str:
        return f"{_REF_KEY_PREFIX}{ref_id}"

    @staticmethod
    def _session_key(*, user_id: str, session_id: str) -> str:
        return f"{_SESSION_KEY_PREFIX}{user_id}:{session_id}"


def _jsonable(value: Any) -> Any:
    """将 dataclass 嵌套结构转换为 JSON 可序列化值。

    递归处理复杂的嵌套字典、列表，并将 datetime 对象标准化为 ISO 8601 字符串。
    """
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}

    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]

    if isinstance(value, datetime):
        return value.isoformat()

    try:
        json.dumps(value)
    except TypeError:
        return str(value)

    return value
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

import redis.asyncio as redis
from beanie import PydanticObjectId

from chat.application.tools.web_tools.web_content_cache.models import (
    WebContentCacheCleanupResult,
    WebContentCacheEntry,
    WebContentCacheMode,
    WebContentCacheValue,
)
from chat.application.tools.web_tools.web_content_cache.repository import (
    WebContentCacheRepository,
)
from chat.domain.entities.web_content_cache import WebContentCacheValueDocument

_ENTRY_KEY_PREFIX = "wisepen:web_content_cache:entry:"
_REFRESH_LOCK_KEY_PREFIX = "wisepen:web_content_cache:refresh_lock:"


class RedisMongoWebContentCacheRepository(WebContentCacheRepository):
    """统一 URL 内容缓存仓储。

    Redis 保存短索引，MongoDB 保存正文内容。当前仓储只负责读写基础缓存记录，
    命中策略、stale-while-revalidate 和 parser 版本失效由后续接入层处理。
    """

    __slots__ = ("_redis",)

    def __init__(self, *, redis_url: str) -> None:
        self._redis = redis.from_url(redis_url, decode_responses=True)

    async def get_entry(
        self,
        *,
        user_id: str,
        url: str,
        cache_mode: WebContentCacheMode | str,
    ) -> WebContentCacheEntry | None:
        mode = WebContentCacheMode(cache_mode)
        raw = await self._redis.get(self._entry_key(user_id=user_id, url=url, cache_mode=mode))
        if raw is None:
            return None

        payload: dict[str, Any] = json.loads(raw)
        return WebContentCacheEntry(
            user_id=str(payload["user_id"]),
            url_hash=str(payload["url_hash"]),
            canonical_url=str(payload["canonical_url"]),
            mongo_doc_id=str(payload["mongo_doc_id"]),
            cache_mode=WebContentCacheMode(str(payload["cache_mode"])),
            soft_expire_at=datetime.fromisoformat(str(payload["soft_expire_at"])),
            hard_expire_at=datetime.fromisoformat(str(payload["hard_expire_at"])),
            etag=(
                str(payload["etag"])
                if payload.get("etag") is not None
                else None
            ),
            last_modified=(
                str(payload["last_modified"])
                if payload.get("last_modified") is not None
                else None
            ),
        )

    async def get_readable_entry(
        self,
        *,
        user_id: str,
        url: str,
    ) -> WebContentCacheEntry | None:
        private_entry = await self.get_entry(
            user_id=user_id,
            url=url,
            cache_mode=WebContentCacheMode.PRIVATE,
        )
        if private_entry is not None:
            return private_entry

        return await self.get_entry(
            user_id=user_id,
            url=url,
            cache_mode=WebContentCacheMode.PUBLIC,
        )

    async def set_entry(self, entry: WebContentCacheEntry) -> None:
        canonical_url = entry.canonical_url.strip()
        payload = json.dumps(
            _jsonable(
                {
                    **asdict(entry),
                    "url_hash": self.url_hash(canonical_url),
                    "canonical_url": canonical_url,
                }
            ),
            ensure_ascii=False,
        )
        ttl_seconds = _redis_ttl_seconds(entry.hard_expire_at)
        await self._redis.set(
            self._entry_key(
                user_id=entry.user_id,
                url=canonical_url,
                cache_mode=entry.cache_mode,
            ),
            payload,
            ex=ttl_seconds,
        )

    async def get_value(self, *, doc_id: str) -> WebContentCacheValue | None:
        document = await WebContentCacheValueDocument.get(PydanticObjectId(doc_id))
        if document is None:
            return None

        return WebContentCacheValue(
            id=str(document.id) if document.id is not None else None,
            user_id=document.user_id,
            canonical_url=document.canonical_url,
            final_url=document.final_url,
            cache_mode=document.cache_mode,
            status_code=document.status_code,
            content_type=document.content_type,
            raw_html=document.raw_html,
            markdown=document.markdown,
            content_hash=document.content_hash,
            fetched_at=document.fetched_at,
            metadata=document.metadata,
        )

    async def save_value(self, value: WebContentCacheValue) -> str:
        now = datetime.now(timezone.utc)

        if value.id:
            document = await WebContentCacheValueDocument.get(PydanticObjectId(value.id))
            if document is not None:
                document.canonical_url = value.canonical_url
                document.user_id = value.user_id
                document.final_url = value.final_url
                document.cache_mode = value.cache_mode
                document.status_code = value.status_code
                document.content_type = value.content_type
                document.raw_html = value.raw_html
                document.markdown = value.markdown
                document.content_hash = value.content_hash
                document.fetched_at = value.fetched_at
                document.metadata = value.metadata
                document.updated_at = now
                await document.save()
                return str(document.id)

        document = WebContentCacheValueDocument(
            user_id=value.user_id,
            canonical_url=value.canonical_url,
            final_url=value.final_url,
            cache_mode=value.cache_mode,
            status_code=value.status_code,
            content_type=value.content_type,
            raw_html=value.raw_html,
            markdown=value.markdown,
            content_hash=value.content_hash,
            fetched_at=value.fetched_at,
            metadata=value.metadata,
            created_at=now,
            updated_at=now,
        )
        await document.insert()
        return str(document.id)

    async def delete_entry(
        self,
        *,
        user_id: str,
        url: str,
        cache_mode: WebContentCacheMode | str,
    ) -> None:
        mode = WebContentCacheMode(cache_mode)
        await self._redis.delete(self._entry_key(user_id=user_id, url=url, cache_mode=mode))

    async def try_acquire_refresh_lock(
        self,
        *,
        key: str,
        ttl_seconds: int,
    ) -> bool:
        locked = await self._redis.set(
            f"{_REFRESH_LOCK_KEY_PREFIX}{self._hash(key)}",
            "1",
            ex=max(1, ttl_seconds),
            nx=True,
        )
        return bool(locked)

    async def cleanup_inactive_values(
        self,
        *,
        updated_before: datetime,
        batch_size: int,
    ) -> WebContentCacheCleanupResult:
        """删除已经没有 active Redis entry 指向的 Mongo cache value。

        Redis entry 是缓存 active 状态的权威索引。Mongo value 只有在超过保留期、
        且同 URL/mode 下的 Redis entry 不再指向该 doc_id 时才会被删除。
        """
        scanned = deleted = active = failed = 0
        cursor = (
            WebContentCacheValueDocument
            .find(WebContentCacheValueDocument.updated_at < updated_before)
            .sort("+updated_at")
            .limit(max(1, batch_size))
        )
        documents = await cursor.to_list()

        for document in documents:
            scanned += 1
            try:
                doc_id = str(document.id)
                entry = await self.get_entry(
                    user_id=document.user_id,
                    url=document.canonical_url,
                    cache_mode=document.cache_mode,
                )
                if entry is not None and entry.mongo_doc_id == doc_id:
                    active += 1
                    continue

                await document.delete()
                deleted += 1
            except Exception:
                failed += 1

        return WebContentCacheCleanupResult(
            scanned=scanned,
            deleted=deleted,
            active=active,
            failed=failed,
        )

    @classmethod
    def _entry_key(cls, *, user_id: str, url: str, cache_mode: WebContentCacheMode) -> str:
        url_hash = cls.url_hash(url)
        if cache_mode == WebContentCacheMode.PUBLIC:
            return f"{_ENTRY_KEY_PREFIX}public:{url_hash}"
        return f"{_ENTRY_KEY_PREFIX}private:{cls._hash(user_id)}:{url_hash}"


    @classmethod
    def url_hash(cls, url: str) -> str:
        canonical_url = url.strip()
        return cls._hash(canonical_url)

    @staticmethod
    def _hash(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()


def _redis_ttl_seconds(hard_expire_at: datetime) -> int:
    expires_at = hard_expire_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    return max(1, int((expires_at - now).total_seconds()))


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}

    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, WebContentCacheMode):
        return value.value

    try:
        json.dumps(value)
    except TypeError:
        return str(value)

    return value

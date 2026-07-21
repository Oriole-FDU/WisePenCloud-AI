from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256

from redis.asyncio import Redis

from chat.application.tools.web_tools.web_fetch.core.models import (
    WebContentCacheMode,
    WebContentCacheValue,
)

from .base import RedisRepository

_VALUE_KEY_PREFIX = "wisepen:web_content_cache:value:"


class RedisWebContentCacheRepository(RedisRepository):
    def __init__(self, *, redis_client: Redis) -> None:
        super().__init__(redis_client=redis_client)

    async def get_value(
        self,
        *,
        user_id: str,
        url: str,
        cache_mode: WebContentCacheMode,
    ) -> WebContentCacheValue | None:
        raw = await self._redis.get(
            self._value_key(
                user_id=user_id,
                url=url,
                cache_mode=cache_mode,
            )
        )
        if raw is None:
            return None

        try:
            payload = json.loads(raw)
            return WebContentCacheValue(
                user_id=str(payload["user_id"]),
                canonical_url=str(payload["canonical_url"]),
                cache_mode=WebContentCacheMode(payload["cache_mode"]),
                text=str(payload["text"]),
                is_md=bool(payload["is_md"]),
                raw_html=payload.get("raw_html"),
                expire_at=datetime.fromisoformat(payload["expire_at"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    async def set_value(self, value: WebContentCacheValue) -> None:
        payload = asdict(value)
        payload["cache_mode"] = value.cache_mode.value
        payload["expire_at"] = value.expire_at.isoformat()
        expire_at = value.expire_at
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=timezone.utc)
        await self._redis.set(
            self._value_key(
                user_id=value.user_id,
                url=value.canonical_url,
                cache_mode=value.cache_mode,
            ),
            json.dumps(payload, ensure_ascii=False),
            ex=max(
                1,
                int(
                    (expire_at - datetime.now(timezone.utc)).total_seconds()
                ),
            ),
        )

    @classmethod
    def _value_key(
        cls,
        *,
        user_id: str,
        url: str,
        cache_mode: WebContentCacheMode,
    ) -> str:
        url_hash = cls._hash(url.strip())
        if cache_mode is WebContentCacheMode.PUBLIC:
            return f"{_VALUE_KEY_PREFIX}public:{url_hash}"
        return f"{_VALUE_KEY_PREFIX}private:{cls._hash(user_id)}:{url_hash}"

    @staticmethod
    def _hash(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()

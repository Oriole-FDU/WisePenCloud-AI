from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256

from redis.asyncio import Redis

from chat.application.tools.web_tools.common import WebContentCacheValue

from .base import RedisRepository

_VALUE_KEY_PREFIX = "wisepen:web_content_cache:value:"


class RedisWebContentCacheRepository(RedisRepository):
    def __init__(self, *, redis_client: Redis) -> None:
        super().__init__(redis_client=redis_client)

    async def get_value(
        self,
        *,
        url: str,
        cache_variant: str = "",
    ) -> WebContentCacheValue | None:
        raw = await self._redis.get(
            self._value_key(
                url=url,
                cache_variant=cache_variant,
            )
        )
        if raw is None:
            return None

        try:
            payload = json.loads(raw)
            return WebContentCacheValue(
                canonical_url=str(payload["canonical_url"]),
                text=str(payload["text"]),
                is_md=bool(payload["is_md"]),
                raw_html=payload.get("raw_html"),
                cache_variant=str(payload.get("cache_variant") or ""),
                expire_at=datetime.fromisoformat(payload["expire_at"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    async def set_value(self, value: WebContentCacheValue) -> None:
        payload = asdict(value)
        payload["expire_at"] = value.expire_at.isoformat()
        expire_at = value.expire_at
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=timezone.utc)
        await self._redis.set(
            self._value_key(
                url=value.canonical_url,
                cache_variant=value.cache_variant,
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
        url: str,
        cache_variant: str = "",
    ) -> str:
        cache_key = url.strip()
        if cache_variant:
            cache_key = f"{cache_key}\0{cache_variant}"
        url_hash = cls._hash(cache_key)
        return f"{_VALUE_KEY_PREFIX}{url_hash}"

    @staticmethod
    def _hash(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()

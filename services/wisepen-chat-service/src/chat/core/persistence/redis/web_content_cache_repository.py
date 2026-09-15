from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256

import redis.asyncio as redis

from chat.core.config.app_settings import settings
from chat.domain.repositories.web_content_cache_repo import (
    WebContentCacheRepository,
    WebContentCacheValue,
)

_VALUE_KEY_PREFIX = "wisepen:web_content_cache:value:"


class RedisWebContentCacheRepository(WebContentCacheRepository):
    """按 URL 保存共享 web 正文，持久化字段与当前 cache 契约保持一致。"""

    def __init__(self) -> None:
        self.redis = redis.from_url(settings.REDIS_URL, decode_responses=True)

    async def get_value(self, *, url: str) -> WebContentCacheValue | None:
        raw = await self.redis.get(self._value_key(url))
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
            return WebContentCacheValue(
                canonical_url=str(payload["canonical_url"]),
                text=str(payload["text"]),
                raw_html=payload.get("raw_html"),
                expire_at=datetime.fromisoformat(payload["expire_at"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    async def set_value(self, value: WebContentCacheValue) -> None:
        payload = asdict(value)
        payload["expire_at"] = value.expire_at.isoformat()
        expire_at = value.expire_at
        if expire_at.tzinfo is None:
            expire_at = expire_at.replace(tzinfo=UTC)
        await self.redis.set(
            self._value_key(value.canonical_url),
            json.dumps(payload, ensure_ascii=False),
            ex=max(1, int((expire_at - datetime.now(UTC)).total_seconds())),
        )

    @staticmethod
    def _value_key(url: str) -> str:
        digest = sha256(url.strip().encode("utf-8")).hexdigest()
        return f"{_VALUE_KEY_PREFIX}{digest}"

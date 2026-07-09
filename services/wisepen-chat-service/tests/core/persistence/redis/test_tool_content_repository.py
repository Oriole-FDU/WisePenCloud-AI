from __future__ import annotations

from typing import Any

import pytest

from chat.application.tools.common.tool_content_store.core.models import (
    StoredToolContent,
    ToolContentChunk,
)
from chat.core.persistence.redis._utils.cache_codec import dumps_cache
from chat.core.persistence.redis.tool_content_repository import RedisToolContentRepository


class _FakeRedis:
    def __init__(self, values: dict[str, bytes | str]) -> None:
        self._values = values

    async def get(self, key: str) -> bytes | str | None:
        return self._values.get(key)


def _repository_with_payload(payload: Any) -> RedisToolContentRepository:
    return RedisToolContentRepository(
        redis_client=_FakeRedis(
            {
                "wisepen:tool_content:item:cnt_1": payload,
            }
        ),
        ttl_seconds=60,
    )


@pytest.mark.asyncio
async def test_tool_content_repository_get_restores_stored_content() -> None:
    stored = StoredToolContent(
        content_id="cnt_1",
        session_id="s1",
        content_type="text/markdown",
        text="hello",
        chunks=(
            ToolContentChunk(
                chunk_index=0,
                start_offset=0,
                end_offset=5,
                block_kinds=("paragraph",),
            ),
        ),
        metadata={"source": "test"},
    )
    repository = _repository_with_payload(dumps_cache(stored))

    assert await repository.get("cnt_1") == stored


@pytest.mark.asyncio
async def test_tool_content_repository_get_returns_none_for_corrupt_payload() -> None:
    repository = _repository_with_payload(
        b'{"content_id":"cnt_1","session_id":"s1","content_type":"text/markdown","text":"hello","chunks":[{"chunk_index":"not-an-int"}]}'
    )

    assert await repository.get("cnt_1") is None

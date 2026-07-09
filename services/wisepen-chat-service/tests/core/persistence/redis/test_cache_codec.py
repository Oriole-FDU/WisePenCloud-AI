from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import msgspec
import pytest

from chat.application.tools.common.tool_content_store.core.models import (
    StoredToolContent,
    ToolContentChunk,
)
from chat.core.persistence.redis._utils.cache_codec import (
    dumps_cache,
    loads_cache,
    loads_cache_or_none,
)


@dataclass(frozen=True, slots=True)
class _TypedTimestamp:
    created_at: datetime


def test_cache_codec_round_trips_structured_dataclass() -> None:
    stored = StoredToolContent(
        content_id="cnt_1",
        session_id="s1",
        content_type="text/markdown",
        text="hello",
        chunks=(
            ToolContentChunk(
                chunk_index=1,
                start_offset=0,
                end_offset=5,
                block_kinds=("paragraph",),
            ),
        ),
        metadata={
            "source": "test",
            "page": 1,
        },
    )

    raw = dumps_cache(stored)

    assert isinstance(raw, bytes)
    assert loads_cache(raw, StoredToolContent) == stored


def test_cache_codec_round_trips_typed_datetime_field() -> None:
    value = _TypedTimestamp(created_at=datetime(2026, 7, 9, tzinfo=timezone.utc))

    assert loads_cache(dumps_cache(value), _TypedTimestamp) == value


def test_cache_codec_rejects_invalid_typed_payload() -> None:
    raw = b'{"content_id":"cnt_1","session_id":"s1","content_type":"text/markdown","text":"hello","chunks":[{"chunk_index":"not-an-int"}]}'

    with pytest.raises(msgspec.ValidationError):
        loads_cache(raw, StoredToolContent)


def test_cache_codec_returns_none_for_invalid_typed_payload() -> None:
    raw = b'{"content_id":"cnt_1","session_id":"s1","content_type":"text/markdown","text":"hello","chunks":[{"chunk_index":"not-an-int"}]}'

    assert loads_cache_or_none(raw, StoredToolContent) is None


def test_cache_codec_encodes_non_finite_float_as_null() -> None:
    assert dumps_cache({"value": math.nan}) == b'{"value":null}'

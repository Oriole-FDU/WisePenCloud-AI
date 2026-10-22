import asyncio
import json

import pytest

from chat.application.tools.core import ToolExecutionError
from chat.application.tools.core.execution.executor import ToolExecutor
from chat.application.tools.core.output_cache.cache_store import StoredToolContent
from chat.application.tools.session_tools.tool_output import search_by_regex
from chat.application.tools.session_tools.tool_output.read_by_range import _read_range
from chat.application.tools.session_tools.tool_output.search_by_regex import (
    _regex_window_range,
    _search_by_regex,
)


def test_range_read_caps_large_requested_interval():
    result = _read_range(
        "0123456789",
        start=1,
        end=100_000_000,
        char_budget=4,
    )

    assert result.text == "1234"
    assert result.start_offset == 1
    assert result.end_offset == 5


def test_regex_searches_content_ids_in_order_and_keeps_source_identity():
    first = StoredToolContent("first", "s", "前文 target 后文")
    second = StoredToolContent("second", "s", "second target")
    result = asyncio.run(
        _search_by_regex(
            stored_items=[first, second],
            pattern="target",
        )
    )

    assert result.truncated is False
    assert [window.content_id for window in result.windows] == ["first", "second"]
    assert all(window.matched_count == 1 for window in result.windows)


def test_regex_merges_overlapping_windows_and_counts_matches():
    stored = StoredToolContent("c", "s", "x" + "a" * 299 + "x")
    result = asyncio.run(_search_by_regex(stored_items=[stored], pattern="x"))

    assert len(result.windows) == 1
    assert result.windows[0].text.count("x") == 2
    assert result.windows[0].matched_count == 2
    assert len(result.windows[0].text) <= 640
    serialized = json.loads(ToolExecutor._coerce_tool_output(result).content)
    assert serialized["windows"][0]["content_id"] == "c"
    assert serialized["windows"][0]["matched_count"] == 2
    assert "highlights" not in serialized["windows"][0]


def test_regex_limits_single_match_display_and_window_length():
    stored = StoredToolContent("c", "s", "a" * 1000)
    result = asyncio.run(_search_by_regex(stored_items=[stored], pattern="a+"))

    assert len(result.windows) == 1
    assert len(result.windows[0].text) <= 640
    assert result.windows[0].end_offset - result.windows[0].start_offset <= 640
    assert result.windows[0].matched_count == 1


def test_regex_expands_to_blingfire_sentence_boundaries():
    text = "Before one. Before two. Target sentence. After one. After two."
    target_start = text.index("Target")
    target_end = target_start + len("Target sentence")

    start, end = _regex_window_range(
        text,
        match_start=target_start,
        match_end=target_end,
    )

    assert text[start:end] == "Before two. Target sentence. After one."


def test_regex_returns_exactly_one_hundred_matches_without_truncating():
    stored = StoredToolContent("c", "s", "x" * 100)
    result = asyncio.run(_search_by_regex(stored_items=[stored], pattern="x"))

    assert result.truncated is False
    assert sum(window.matched_count for window in result.windows) == 100


def test_regex_detects_the_101st_match_and_returns_only_the_first_hundred():
    stored = StoredToolContent("c", "s", "x" * 101)
    result = asyncio.run(_search_by_regex(stored_items=[stored], pattern="x"))

    assert result.truncated is True
    assert sum(window.matched_count for window in result.windows) == 100


def test_regex_reports_no_matches_without_truncation():
    result = asyncio.run(
        _search_by_regex(
            stored_items=[StoredToolContent("c", "s", "nothing here")],
            pattern="missing",
        )
    )

    assert result.truncated is False
    assert result.windows == []


@pytest.mark.asyncio
async def test_regex_tool_uses_list_content_ids_and_reports_invalid_or_missing_input(monkeypatch):
    stored = StoredToolContent("c", "s", "x")

    async def get_content(*, content_id, session_id):
        assert session_id == "s"
        return stored if content_id == "c" else None

    monkeypatch.setattr(search_by_regex, "get_tool_content", get_content)
    tool = search_by_regex.SearchToolOutputRegexTool()
    result = await tool.execute(
        {"session_id": "s"},
        content_ids=["c", "missing"],
        pattern="x",
    )
    assert result.windows[0].content_id == "c"

    with pytest.raises(ToolExecutionError) as invalid:
        await tool.execute({"session_id": "s"}, content_ids=["c"], pattern="[")
    assert invalid.value.reason == "invalid_regex_pattern"


def test_regex_timeout_does_not_return_partial_results(monkeypatch):
    stored = StoredToolContent("c", "s", "x")

    class TimedOutRegex:
        def finditer(self, text, *, timeout):
            raise TimeoutError("regex timeout")

    monkeypatch.setattr(search_by_regex.regex, "compile", lambda pattern: TimedOutRegex())
    with pytest.raises(TimeoutError, match="results are incomplete"):
        asyncio.run(_search_by_regex(stored_items=[stored], pattern="x"))

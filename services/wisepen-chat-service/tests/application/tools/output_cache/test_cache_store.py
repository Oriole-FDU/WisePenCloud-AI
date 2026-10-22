import json

import pytest
from common.utils.markdown import MarkdownChunker
from pydantic import TypeAdapter

from chat.application.tools.core.output_cache import cache_store
from chat.application.tools.core.output_cache.cache_store import StoredToolContent
from chat.application.tools.session_tools.tool_output.read_by_range import (
    _normalize_offset,
)
from chat.application.tools.session_tools.tool_output.read_by_section import (
    _read_by_section,
)


def test_stored_content_round_trips_and_reads_legacy_without_chunks():
    text = "<!-- ignored by parser -->\n\n# Heading\n\nbody\n"
    result = MarkdownChunker().chunk(text)
    stored = StoredToolContent(
        content_id="c",
        session_id="s",
        text=text,
        chunks=list(result.chunks),
        sections=list(result.sections),
        anchors=list(result.anchors),
    )
    adapter = TypeAdapter(StoredToolContent)
    decoded = json.loads(adapter.dump_json(stored))
    assert adapter.validate_json(json.dumps(decoded)) == stored

    legacy = dict(decoded)
    legacy.pop("chunks")
    assert adapter.validate_json(json.dumps(legacy)).chunks == []


@pytest.mark.asyncio
async def test_put_tool_content_returns_content_id_and_source_length(monkeypatch):
    class Repository:
        stored = None

        async def put(self, stored):
            self.stored = stored

    repository = Repository()
    monkeypatch.setattr(cache_store, "_repository", lambda: repository)

    receipt = await cache_store.put_tool_content(
        session_id="s",
        text="# Heading\n\nbody\n",
    )

    assert receipt == (repository.stored.content_id, len(repository.stored.text))
    assert repository.stored.chunks


def test_section_reader_uses_own_and_subtree_source_spans_and_offsets():
    text = "# Heading\n\nbody12345\n\n## Nested\n\nchild body\n"
    result = MarkdownChunker().chunk(text)
    stored = StoredToolContent("c", "s", text, sections=result.sections)
    section = result.sections[0]

    first_window = _read_by_section(
        stored=stored,
        section_id=section.section_id,
        scope="own",
        start_offset=0,
        char_budget=8,
    )
    second_window = _read_by_section(
        stored=stored,
        section_id=section.section_id,
        scope="own",
        start_offset=first_window.next_offset,
        char_budget=8,
    )
    last_window = _read_by_section(
        stored=stored,
        section_id=section.section_id,
        scope="own",
        start_offset=section.own_span.end_offset - 1,
        char_budget=8,
    )
    subtree = _read_by_section(
        stored=stored,
        section_id=section.section_id,
        scope="subtree",
        start_offset=0,
        char_budget=len(text),
    )

    assert first_window.content_id == "c"
    assert first_window.section_id == section.section_id
    assert first_window.window.text == text[section.own_span.start_offset : 8]
    assert first_window.has_more is True
    assert second_window.window.start_offset == first_window.next_offset
    assert second_window.window.end_offset == first_window.next_offset + 8
    assert last_window.window.text == text[section.own_span.end_offset - 1 : section.own_span.end_offset]
    assert last_window.has_more is False
    assert "child body" in subtree.window.text


def test_range_offset_normalization_keeps_python_negative_index_semantics():
    assert _normalize_offset(-3, 10, default=0) == 7
    assert _normalize_offset(100_000_000, 10, default=0) == 10

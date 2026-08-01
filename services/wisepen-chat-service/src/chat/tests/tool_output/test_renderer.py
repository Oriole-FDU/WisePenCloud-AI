from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import pytest
from pydantic import BaseModel

from chat.application.tools.common.tool_content_store import (
    StoredToolContent,
    ToolContentPutResult,
    ToolContentPutStatus,
    ToolContentReceipt,
    ToolContentStore,
)
from chat.application.tools.core.definition import (
    ToolDefinition,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
)
from chat.application.tools.core.execution.executor import ToolExecutor
from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.core.output.cache import ToolOutputCache
from chat.application.tools.core.llm.renderer import render_tool_result
from chat.application.tools.core.output.tool_return import (
    CacheableText,
    ToolReturn,
)
from chat.application.tools.core.registry import ToolScope


class _PayloadModel(BaseModel):
    name: str


@dataclass(frozen=True)
class _PayloadData:
    value: int


class _PayloadKey(StrEnum):
    STATUS = "status"


class _RepositoryStub:
    def __init__(self) -> None:
        self.stored: StoredToolContent | None = None

    async def put(self, stored: StoredToolContent) -> None:
        self.stored = stored

    async def get(self, content_id: str) -> StoredToolContent | None:
        return (
            self.stored
            if self.stored and self.stored.content_id == content_id
            else None
        )


class _ToolStub:
    def __init__(self, output: object) -> None:
        self._output = output
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="example_tool",
                description="example",
                parameters_schema=ToolParametersSchema(
                    {"type": "object", "properties": {}}
                ),
            ),
            policy=ToolPolicy(persist_output=True),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict, config: dict | None = None, **kwargs):
        return self._output


def _invocation() -> ToolInvocation:
    return ToolInvocation(
        tool_call_id="call-1",
        tool_name="example_tool",
        tool_call_arguments={},
    )


def test_renderer_encodes_common_tool_outputs() -> None:
    result = render_tool_result(
        invocation=_invocation(),
        output={
            _PayloadKey.STATUS: "ok",
            1: _PayloadData(value=2),
            "model": _PayloadModel(name="test"),
            "date": date(2026, 7, 19),
        },
        tool_definition=None,
    )

    assert json.loads(result.tool_output) == {
        "status": "ok",
        "1": {"value": 2},
        "model": {"name": "test"},
        "date": "2026-07-19",
    }


def test_renderer_removes_empty_values_after_json_encoding() -> None:
    result = render_tool_result(
        invocation=_invocation(),
        output={
            "none": None,
            "empty_string": "",
            "empty_list": [],
            "empty_tuple": (),
            "empty_object": {},
            "recursively_empty": {"items": [None, "", {}, [], ()]},
            "nested": {
                "removed": None,
                "items": [None, "", {}, [], {"value": "kept"}],
            },
            "zero": 0,
            "false": False,
        },
        tool_definition=None,
    )

    assert json.loads(result.tool_output) == {
        "nested": {"items": [{"value": "kept"}]},
        "zero": 0,
        "false": False,
    }


def test_renderer_falls_back_to_original_text_for_unsupported_objects() -> None:
    output = {"value": object()}

    result = render_tool_result(
        invocation=_invocation(),
        output=output,
        tool_definition=None,
    )

    assert result.tool_output == str(output)


@pytest.mark.asyncio
async def test_executor_renders_plain_output_without_tool_return() -> None:
    repository = _RepositoryStub()
    executor = ToolExecutor(
        ToolScope(
            tools={"example_tool": _ToolStub({"status": "ok"})},
            context={"session_id": "session-1"},
        ),
        output_cache=ToolOutputCache(
            content_store=ToolContentStore(repository=repository),
            inline_max_chars=3,
        ),
    )

    result = await executor.execute_one(_invocation())

    assert json.loads(result.tool_output) == {"status": "ok"}
    assert repository.stored is None


@pytest.mark.asyncio
async def test_executor_caches_tool_return_large_text() -> None:
    repository = _RepositoryStub()
    executor = ToolExecutor(
        ToolScope(
            tools={
                "example_tool": _ToolStub(
                    ToolReturn(
                        visible_result={"status": "ok"},
                        cacheable_texts=(
                            CacheableText(
                                text="# large text",
                                metadata={"source_url": "https://example.com"},
                            ),
                        ),
                    )
                )
            },
            context={"session_id": "session-1"},
        ),
        output_cache=ToolOutputCache(
            content_store=ToolContentStore(repository=repository),
            inline_max_chars=3,
        ),
    )

    result = await executor.execute_one(_invocation())

    assert repository.stored is not None
    payload = json.loads(result.tool_output)
    assert payload["status"] == "ok"
    assert payload["content_receipts"][0]["content_index"] == 0
    assert payload["content_receipts"][0]["content_id"] == repository.stored.content_id
    assert payload["content_receipts"][0]["total_length"] == len(repository.stored.text)
    assert payload["content_receipts"][0]["metadata"] == {
        "source_url": "https://example.com"
    }
    assert repository.stored.content_type == "text/plain"
    assert repository.stored.chunks[0].section_paths == ()
    assert repository.stored.metadata == {"source_url": "https://example.com"}


@pytest.mark.asyncio
async def test_executor_preserves_cacheable_text_markdown_type() -> None:
    repository = _RepositoryStub()
    executor = ToolExecutor(
        ToolScope(
            tools={
                "example_tool": _ToolStub(
                    ToolReturn(
                        cacheable_texts=(
                            CacheableText(
                                text="# Heading\n\nMarkdown body",
                                is_md=True,
                            ),
                        ),
                    )
                )
            },
            context={"session_id": "session-1"},
        ),
        output_cache=ToolOutputCache(
            content_store=ToolContentStore(repository=repository),
            inline_max_chars=3,
        ),
    )

    await executor.execute_one(_invocation())

    assert repository.stored is not None
    assert repository.stored.content_type == "text/markdown"
    assert repository.stored.chunks[0].section_paths == (("Heading",),)


@pytest.mark.asyncio
async def test_output_cache_preserves_index_after_partial_store_failure() -> None:
    class _Store:
        calls = 0

        async def put(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("store unavailable")
            return ToolContentPutResult(
                status=ToolContentPutStatus.STORED,
                receipt=ToolContentReceipt(
                    content_id="cnt_second",
                    chunk_count=1,
                    total_length=len("second"),
                    metadata=dict(kwargs["metadata"]),
                ),
            )

    payload = await ToolOutputCache(
        content_store=_Store(),
        inline_max_chars=1,
    ).process(
        tool_return=ToolReturn(
            cacheable_texts=(
                CacheableText(text="first"),
                CacheableText(
                    text="second",
                    metadata={"source_url": "https://example.com/second"},
                ),
            )
        ),
        invocation=_invocation(),
        session_id="session-1",
    )

    assert payload["content_receipts"] == (
        {
            "content_index": 1,
            "content_id": "cnt_second",
            "chunk_count": 1,
            "total_length": 6,
            "supported_selectors": (),
            "metadata": {"source_url": "https://example.com/second"},
        },
    )


@pytest.mark.asyncio
async def test_output_cache_inlines_structured_contents() -> None:
    payload = await ToolOutputCache(
        content_store=_RepositoryStub(),
        inline_max_chars=100,
    ).process(
        tool_return=ToolReturn(
            visible_result={"status": "ok"},
            cacheable_texts=(
                CacheableText(
                    text="first",
                    metadata={"source_url": "https://example.com/1"},
                ),
                CacheableText(
                    text="second",
                    metadata={"source_url": "https://example.com/2"},
                ),
            ),
        ),
        invocation=_invocation(),
        session_id="session-1",
    )

    assert payload["contents"] == (
        {
            "text": "first",
            "metadata": {"source_url": "https://example.com/1"},
        },
        {
            "text": "second",
            "metadata": {"source_url": "https://example.com/2"},
        },
    )

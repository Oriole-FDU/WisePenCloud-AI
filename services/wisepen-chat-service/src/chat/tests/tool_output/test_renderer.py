from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import pytest
from pydantic import BaseModel

from chat.application.tools.common.tool_content_store import (
    StoredToolContent,
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
from chat.application.tools.core.output.tool_return import ToolReturn
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
                        cacheable_texts=("large text",),
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
    assert payload["content_receipts"][0]["content_id"] == repository.stored.content_id

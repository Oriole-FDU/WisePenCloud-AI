import json
from types import SimpleNamespace

import pytest
from common.utils.markdown import MarkdownChunker

from chat.application.tools.core.execution.executor import ToolExecutor
from chat.application.tools.core.execution.hooks.builtin import JsonSchemaCheck
from chat.application.tools.core.llm.invocation import ToolInvocation
from chat.application.tools.session_tools.tool_output import (
    inspect_structure,
)
from chat.application.tools.session_tools.tool_output.read_by_section import (
    ReadToolOutputSectionTool,
)


@pytest.mark.asyncio
async def test_inspect_structure_returns_common_markdown_outline(monkeypatch) -> None:
    result = MarkdownChunker().chunk(
        "# Heading\n\nBody text.\n\nTable 1: Values\n\n| value |\n| --- |\n| 42 |\n"
    )
    stored = SimpleNamespace(
        text="# Heading\n\nBody text.\n\n",
        sections=result.sections,
        anchors=result.anchors,
    )

    async def get_content(*, content_id: str, session_id: str):
        assert content_id == "content-1"
        assert session_id == "session-1"
        return stored

    monkeypatch.setattr(inspect_structure, "get_tool_content", get_content)
    tool = inspect_structure.InspectToolOutputStructureTool()

    response = await tool.execute(
        {"session_id": "session-1"},
        content_id="content-1",
    )

    assert isinstance(response, inspect_structure.ToolOutputStructureResult)
    assert response.content_id == "content-1"
    assert response.outline.startswith("# Heading")
    assert "id=sec_" in response.outline
    assert "@0" in response.outline
    assert "[Table 1]" in response.outline
    assert response.total_length == len(stored.text)

    serialized = json.loads(ToolExecutor._coerce_tool_output(response).content)
    assert serialized["outline"] == response.outline
    assert serialized["content_id"] == "content-1"
    assert serialized["total_length"] == len(stored.text)


@pytest.mark.asyncio
async def test_section_schema_injects_defaults_and_rejects_invalid_offsets() -> None:
    tool = ReadToolOutputSectionTool()
    schema = tool.definition.llm_spec.parameters_schema
    invocation = ToolInvocation(
        tool_call_id="call-1",
        tool_name="read_tool_output_section",
        tool_call_arguments={"content_id": "content-1", "section_id": "sec-1"},
    )

    valid = await JsonSchemaCheck().check(
        invocation,
        tool.definition.policy,
        schema,
        {},
    )

    assert valid.ok is True
    assert invocation.tool_call_arguments["scope"] == "own"
    assert invocation.tool_call_arguments["start_offset"] == 0

    invocation.tool_call_arguments["start_offset"] = -1
    invalid = await JsonSchemaCheck().check(
        invocation,
        tool.definition.policy,
        schema,
        {},
    )

    assert invalid.ok is False

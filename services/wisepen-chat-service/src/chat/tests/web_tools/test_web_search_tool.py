from types import SimpleNamespace

import pytest

from chat.application.tools.search_tools.web_search.tools.base import BaseWebSearchTool


class _SearchPipeline:
    async def search(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            search_query=kwargs["search_query"],
            candidates=[{"title": "result"}],
            response=None,
        )


class _SourceFactory:
    def build(self, **kwargs):
        self.kwargs = kwargs
        return "source"


@pytest.mark.asyncio
async def test_web_search_tool_uses_flat_query_arguments() -> None:
    pipeline = _SearchPipeline()
    source_factory = _SourceFactory()
    tool = BaseWebSearchTool(
        tool_name="test_search",
        provider=None,
        search_pipeline=pipeline,
        source_factory=source_factory,
    )

    result = await tool.execute(
        {},
        search_query="  wise pen  ",
        ranking_query="  What is WisePen?  ",
    )

    schema = tool.definition.llm_spec.parameters_schema.raw
    assert schema["required"] == ["search_query", "ranking_query"]
    assert "query" not in schema["properties"]
    assert pipeline.kwargs["search_query"] == "wise pen"
    assert pipeline.kwargs["ranking_query"] == "What is WisePen?"
    assert result["query"] == "wise pen"

from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP

from wisepen_mcp.capabilities.rag.tools import register_rag_tools
from wisepen_mcp.capabilities.rag.tools.common import session_id
from wisepen_mcp.capabilities.rag.tools.expand import _render_expand_result
from wisepen_mcp.capabilities.core.tools import (
    MCP_TOOL_CONTEXT_META_KEY,
)


@pytest.mark.asyncio
async def test_registration_exposes_three_navigation_tools() -> None:
    mcp = FastMCP("test")
    register_rag_tools(mcp, SimpleNamespace())

    tools = {tool.name: tool for tool in await mcp.list_tools()}

    assert set(tools) == {
        "knowledge_navigate_locate",
        "knowledge_navigate_expand",
        "knowledge_navigate_sections",
    }
    assert set(tools["knowledge_navigate_locate"].inputSchema["properties"]) == {
        "query",
        "max_results",
    }
    expand = tools["knowledge_navigate_expand"]
    assert set(expand.inputSchema["properties"]) == {
        "state_id",
        "node_ids",
        "query",
        "relation_types",
        "direction",
        "max_depth",
        "max_results",
    }
    assert "source-to-target" in expand.inputSchema["properties"]["direction"]["description"]
    assert "does not alter graph traversal rules" in expand.inputSchema["properties"]["query"]["description"]


def test_expand_renderer_uses_shared_tool_return_and_keeps_evidence_identity() -> None:
    result = _render_expand_result(
        {
            "state_id": "state-1",
            "nodes": [
                {"node_id": "alpha", "label": "Alpha"},
                {"node_id": "beta", "label": "Beta"},
            ],
            "edges": [
                {
                    "edge_id": "edge-1",
                    "source_node_id": "alpha",
                    "target_node_id": "beta",
                    "relation_type": "DEPENDS_ON",
                    "predicate": None,
                    "evidence_quotes": ["Alpha depends on Beta."],
                    "evidence_source_ref_ids": ["source-1"],
                }
            ],
            "paths": [{"node_ids": ["alpha", "beta"], "edge_ids": ["edge-1"]}],
            "sources": [_section_view_payload()],
        }
    )

    edge = result["visible_result"]["edges"][0]
    assert edge == {
        "edge_id": "edge-1",
        "source_node_id": "alpha",
        "source_label": "Alpha",
        "target_node_id": "beta",
        "target_label": "Beta",
        "relation_type": "DEPENDS_ON",
        "predicate": None,
        "direction": "out",
        "evidence_quotes": ["Alpha depends on Beta."],
        "evidence_source_ref_ids": ["source-1"],
        "evidence_content_indices": [1],
    }
    source = result["visible_result"]["sources"][0]
    assert source["reading_blocks"][0]["reading_block_id"] == "block-1"
    assert source["evidence"][0]["source_ref_id"] == "source-1"
    assert source["frontier"] == {"parent": None, "previous": None, "next": None, "children": []}
    assert result["cacheable_texts"][0]["metadata"] == {
        "resource_id": "resource-1",
        "section_id": "section-1",
        "reading_block_id": "block-1",
        "section_path": ["Chapter 1"],
        "page_labels": ["1"],
        "anchor_labels": ["Paragraph 1"],
    }
    assert result["cacheable_texts"][1]["metadata"] == {
        "resource_id": "resource-1",
        "section_id": "section-1",
        "source_ref_id": "source-1",
        "section_path": ["Chapter 1"],
        "page_labels": ["1"],
        "anchor_labels": ["Paragraph 1"],
    }


def test_session_id_is_read_from_mcp_tool_context() -> None:
    context = SimpleNamespace(
        request_context=SimpleNamespace(
            meta=SimpleNamespace(
                model_extra={MCP_TOOL_CONTEXT_META_KEY: {"session_id": " session-1 "}}
            )
        )
    )

    assert session_id(context) == "session-1"


def _section_view_payload() -> dict[str, object]:
    return {
        "resource_id": "resource-1",
        "section_id": "section-1",
        "title": "Chapter 1",
        "section_path": ["Chapter 1"],
        "preview": "Preview",
        "has_content": True,
        "reading_blocks": [
            {
                "block_id": "block-1",
                "section_id": "section-1",
                "raw_text": "Full reading block.",
                "page_labels": ["1"],
                "anchor_labels": ["Paragraph 1"],
            }
        ],
        "evidence": [
            {
                "content": "Evidence text.",
                "ref_id": "source-1",
                "resource_id": "resource-1",
                "section_id": "section-1",
                "section_path": ["Chapter 1"],
                "chunk_id": "chunk-1",
                "page_labels": ["1"],
                "anchor_labels": ["Paragraph 1"],
            }
        ],
        "frontier": {"parent": None, "previous": None, "next": None, "children": []},
    }

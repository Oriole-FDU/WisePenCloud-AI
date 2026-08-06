from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field, StringConstraints

from wisepen_mcp.capabilities.rag.models import (
    KnowledgeNavigationDirection,
    KnowledgeRelationType,
)
from wisepen_mcp.service_client import RagServiceClient

from .common import (
    RagTextRenderBudget,
    RagTextRenderRouter,
    StateId,
    section_view_payload,
    session_id,
)

_LOCATE_DESCRIPTION = (
    "Description:\n"
    "Call first when the answer may be in the user's private WisePen documents. "
    "It returns grounded sections plus graph node anchors for follow-up navigation.\n\n"
    "Output:\n"
    "Use sources[].evidence and sources[].reading_blocks text directly when present; "
    "oversized entries expose cache content_index for follow-up range reads. Reuse "
    "state_id with knowledge_navigate_sections for section text, or with "
    "knowledge_navigate_cypher for returned node_id values. Nodes are anchors, not evidence."
)

_CYPHER_DESCRIPTION = (
    "Description:\n"
    "Follow graph relations from node_id values returned by locate or an earlier cypher. "
    "Use this when entity relationships could reveal related private-document evidence.\n\n"
    "Output:\n"
    "paths are candidate reasoning chains. edges expose endpoint node IDs, direction, "
    "relation type, evidence quotes, and source_ref IDs. Oversized returned sources "
    "also expose evidence_content_indices for follow-up range reads."
)

_SECTIONS_DESCRIPTION = (
    "Description:\n"
    "Read full text for section_id values already returned by locate, cypher, or "
    "a frontier entry. Use this when a section preview is relevant but incomplete.\n\n"
    "Output:\n"
    "reading_blocks contain section text directly when it fits the safe read budget. "
    "Oversized blocks expose content_index for follow-up range reads. evidence contains "
    "the original hit snippets. frontier suggests adjacent or child sections to read next; "
    "frontier entries are navigation choices, not evidence."
)

_QUERY = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    Field(
        description=(
            "The complete question or concept to answer from the user's private "
            "documents. Include the subject and constraints needed to judge relevance."
        ),
    ),
]

_NODE_IDS = Annotated[
    tuple[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)], ...],
    Field(
        min_length=1,
        max_length=16,
        description=(
            "Node IDs already returned in this navigation state. Each ID is a graph "
            "expansion seed; do not invent IDs from labels."
        ),
    ),
]

_SECTION_IDS = Annotated[
    tuple[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)], ...],
    Field(
        min_length=1,
        max_length=12,
        description=(
            "Section IDs already returned in sources or frontier entries. Select the "
            "sections whose full reading blocks are needed."
        ),
    ),
]


def register_navigation_tools(
    mcp: FastMCP,
    client: RagServiceClient,
    *,
    text_budget: RagTextRenderBudget,
) -> None:
    @mcp.tool(name="knowledge_navigate_locate", description=_LOCATE_DESCRIPTION)
    async def knowledge_navigate_locate(
        query: _QUERY,
        ctx: Context,
        max_results: Annotated[
            int,
            Field(
                ge=1, le=20,
                description="Maximum number of relevant private-document results to return.",
            ),
        ] = 10,
    ) -> dict[str, Any]:
        return _render_locate_result(
            await client.locate(
                session_id=session_id(ctx),
                query=query,
                max_results=max_results,
            ),
            text_budget=text_budget,
        )

    @mcp.tool(name="knowledge_navigate_cypher", description=_CYPHER_DESCRIPTION)
    async def knowledge_navigate_cypher(
        state_id: StateId,
        node_ids: _NODE_IDS,
        ctx: Context,
        query: Annotated[
            str | None,
            StringConstraints(strip_whitespace=True, min_length=1),
            Field(
                description=(
                    "Optional intent for ranking graph paths after relation-constrained "
                    "candidates are generated. It does not alter graph traversal rules; "
                    "omit it to reuse the original locate query."
                ),
            ),
        ] = None,
        relation_types: Annotated[
            tuple[KnowledgeRelationType, ...],
            Field(
                max_length=16,
                description=(
                    "Allowed formal relation types. Leave empty to allow every supported "
                    "relation type."
                ),
            ),
        ] = (),
        direction: Annotated[
            KnowledgeNavigationDirection,
            Field(
                description=(
                    "Traversal direction relative to each seed: out follows semantic "
                    "source-to-target edges, in follows edges whose target is the seed, "
                    "and both allows either direction."
                ),
            ),
        ] = KnowledgeNavigationDirection.BOTH,
        max_depth: Annotated[
            int,
            Field(
                ge=1, le=2,
                description="Maximum relation hops per candidate path: 1 for direct, 2 for two-hop.",
            ),
        ] = 1,
        max_results: Annotated[
            int,
            Field(ge=1, le=20, description="Maximum number of ranked relation paths to return."),
        ] = 10,
    ) -> dict[str, Any]:
        return _render_cypher_result(
            await client.cypher(
                session_id=session_id(ctx),
                state_id=state_id,
                node_ids=node_ids,
                query=query,
                relation_types=tuple(value.value for value in relation_types),
                direction=direction.value,
                max_depth=max_depth,
                max_results=max_results,
            ),
            text_budget=text_budget,
        )

    @mcp.tool(name="knowledge_navigate_sections", description=_SECTIONS_DESCRIPTION)
    async def knowledge_navigate_sections(
        state_id: StateId,
        section_ids: _SECTION_IDS,
        ctx: Context,
    ) -> dict[str, Any]:
        return _render_sections_result(
            await client.read_sections(
                session_id=session_id(ctx),
                state_id=state_id,
                section_ids=section_ids,
            ),
            text_budget=text_budget,
        )


def _render_locate_result(
    result: dict[str, Any],
    *,
    text_budget: RagTextRenderBudget,
) -> dict[str, Any]:
    text_router = RagTextRenderRouter(text_budget)
    return {
        "visible_result": {
            "state_id": result["state_id"],
            "nodes": result["nodes"],
            "sources": [
                section_view_payload(source, text_router)
                for source in result["sources"]
            ],
        },
        "cacheable_texts": text_router.cacheable_texts,
    }


def _render_cypher_result(
    result: dict[str, Any],
    *,
    text_budget: RagTextRenderBudget,
) -> dict[str, Any]:
    text_router = RagTextRenderRouter(text_budget)
    source_content_indices: dict[str, int] = {}
    sources = [
        section_view_payload(source, text_router, source_content_indices)
        for source in result["sources"]
    ]
    edge_directions: dict[str, str] = {}
    edges_by_id = {edge["edge_id"]: edge for edge in result["edges"]}
    for path in result["paths"]:
        for index, edge_id in enumerate(path["edge_ids"]):
            edge = edges_by_id[edge_id]
            edge_directions.setdefault(
                edge_id,
                "out" if edge["source_node_id"] == path["node_ids"][index] else "in",
            )

    node_labels = {node["node_id"]: node["label"] for node in result["nodes"]}
    return {
        "visible_result": {
            "state_id": result["state_id"],
            "nodes": result["nodes"],
            "edges": [
                _edge_payload(edge, edge_directions, node_labels, source_content_indices)
                for edge in result["edges"]
            ],
            "paths": result["paths"],
            "sources": sources,
        },
        "cacheable_texts": text_router.cacheable_texts,
    }


def _render_sections_result(
    result: dict[str, Any],
    *,
    text_budget: RagTextRenderBudget,
) -> dict[str, Any]:
    text_router = RagTextRenderRouter(text_budget)
    return {
        "visible_result": {
            "state_id": result["state_id"],
            "sections": [
                section_view_payload(section, text_router)
                for section in result["sections"]
            ],
        },
        "cacheable_texts": text_router.cacheable_texts,
    }


def _edge_payload(
    edge: dict[str, Any],
    edge_directions: dict[str, str],
    node_labels: dict[str, str],
    source_content_indices: dict[str, int],
) -> dict[str, Any]:
    source_ref_ids = tuple(dict.fromkeys(edge["evidence_source_ref_ids"]))
    payload = {
        "edge_id": edge["edge_id"],
        "source_node_id": edge["source_node_id"],
        "source_label": node_labels.get(edge["source_node_id"], edge["source_node_id"]),
        "target_node_id": edge["target_node_id"],
        "target_label": node_labels.get(edge["target_node_id"], edge["target_node_id"]),
        "relation_type": edge["relation_type"],
        "predicate": edge.get("predicate"),
        "direction": edge_directions[edge["edge_id"]],
        "evidence_quotes": list(dict.fromkeys(edge["evidence_quotes"])),
        "evidence_source_ref_ids": list(source_ref_ids),
    }
    evidence_content_indices = [
        source_content_indices[source_ref_id]
        for source_ref_id in source_ref_ids
        if source_ref_id in source_content_indices
    ]
    if evidence_content_indices:
        payload["evidence_content_indices"] = evidence_content_indices
    return payload

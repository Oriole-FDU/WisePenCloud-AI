from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field, StringConstraints

from wisepen_mcp.capabilities.core.tools import CacheableText
from wisepen_mcp.capabilities.rag.models import (
    KnowledgeNavigationDirection,
    KnowledgeRelationType,
)
from wisepen_mcp.service_client import RagServiceClient

from .common import StateId, section_view_payload, session_id

_DESCRIPTION = (
    "Description:\n"
    "Follow graph relations from node_id values returned by locate or an earlier expand. "
    "Use this when entity relationships could reveal related private-document evidence.\n\n"
    "Output:\n"
    "paths are candidate reasoning chains. edges expose endpoint node IDs, direction, "
    "relation type, evidence quotes, source_ref IDs, and evidence_content_indices. "
    "Use those content indices and returned sources to ground claims."
)

_NODE_IDS = Annotated[
    tuple[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)], ...],
    Field(
        min_length=1, max_length=16,
        description=(
            "Node IDs already returned in this navigation state. Each ID is a graph "
            "expansion seed; do not invent IDs from labels."
        ),
    ),
]


def register_expand_tool(mcp: FastMCP, client: RagServiceClient) -> None:
    @mcp.tool(name="knowledge_navigate_expand", description=_DESCRIPTION)
    async def knowledge_navigate_expand(
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
        return _render_expand_result(
            await client.expand(
                session_id=session_id(ctx),
                state_id=state_id,
                node_ids=node_ids,
                query=query,
                relation_types=tuple(value.value for value in relation_types),
                direction=direction.value,
                max_depth=max_depth,
                max_results=max_results,
            )
        )


def _render_expand_result(result: dict[str, Any]) -> dict[str, Any]:
    cacheable_texts: list[CacheableText] = []
    source_content_indices: dict[str, int] = {}
    sources = [
        section_view_payload(source, cacheable_texts, source_content_indices)
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
        "cacheable_texts": cacheable_texts,
    }


def _edge_payload(
    edge: dict[str, Any],
    edge_directions: dict[str, str],
    node_labels: dict[str, str],
    source_content_indices: dict[str, int],
) -> dict[str, Any]:
    source_ref_ids = tuple(dict.fromkeys(edge["evidence_source_ref_ids"]))
    return {
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
        "evidence_content_indices": [
            source_content_indices[source_ref_id]
            for source_ref_id in source_ref_ids
            if source_ref_id in source_content_indices
        ],
    }

from typing import Any

from wisepen_mcp.capabilities.core.tools import CacheableText, ToolReturn

_SOURCE_PREVIEW_CHARS = 600


def render_locate_result(result: dict[str, Any]) -> dict[str, Any]:
    cacheable_texts: list[CacheableText] = []
    return {
        "visible_result": {
            "state_id": result["state_id"],
            "nodes": result["nodes"],
            "sources": [
                _section_view_payload(source, cacheable_texts)
                for source in result["sources"]
            ],
        },
        "cacheable_texts": cacheable_texts,
    }


def render_expand_result(result: dict[str, Any]) -> dict[str, Any]:
    cacheable_texts: list[CacheableText] = []
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
                {
                    "edge_id": edge["edge_id"],
                    "relation_type": edge["relation_type"],
                    "predicate": edge.get("predicate"),
                    "direction": edge_directions[edge["edge_id"]],
                    "relation_evidence": _relation_evidence(edge, node_labels),
                }
                for edge in result["edges"]
            ],
            "paths": result["paths"],
            "sources": [
                _section_view_payload(source, cacheable_texts)
                for source in result["sources"]
            ],
        },
        "cacheable_texts": cacheable_texts,
    }


def render_sections_result(result: dict[str, Any]) -> dict[str, Any]:
    cacheable_texts: list[CacheableText] = []
    return {
        "visible_result": {
            "state_id": result["state_id"],
            "sections": [
                _section_view_payload(section, cacheable_texts)
                for section in result["sections"]
            ],
        },
        "cacheable_texts": cacheable_texts,
    }


def _section_view_payload(
    view: dict[str, Any],
    cacheable_texts: list[CacheableText],
) -> dict[str, Any]:
    resource_id = view["resource_id"]
    section_path = view["section_path"]
    return {
        "resource_id": resource_id,
        "section_id": view["section_id"],
        "title": view["title"],
        "section_path": section_path,
        "summary": view["summary"],
        "has_content": view["has_content"],
        "reading_blocks": [
            {
                "content_index": _append_cacheable_text(
                    cacheable_texts,
                    block["raw_text"],
                    metadata={
                        "kind": "rag_section_reading_block",
                        "resource_id": resource_id,
                        "section_id": block["section_id"],
                        "section_path": section_path,
                        "reading_block_id": block["block_id"],
                        "page_labels": block["page_labels"],
                        "anchor_labels": block["anchor_labels"],
                    },
                ),
                "preview": _preview(block["raw_text"]),
            }
            for block in view["reading_blocks"]
        ],
        "evidence": [
            {
                "content_index": _append_cacheable_text(
                    cacheable_texts,
                    source["content"],
                    metadata={
                        "kind": "rag_evidence",
                        "resource_id": source["resource_id"],
                        "section_id": source["section_id"],
                        "section_path": source["section_path"],
                        "source_ref_id": source["ref_id"],
                        "chunk_id": source["chunk_id"],
                        "page_label": source.get("page_label"),
                        "anchor_labels": source["anchor_labels"],
                    },
                ),
                "preview": _preview(source["content"]),
            }
            for source in view["evidence"]
        ],
        "frontier": view["frontier"],
    }


def _append_cacheable_text(
    cacheable_texts: list[CacheableText],
    text: str,
    *,
    metadata: dict[str, Any],
) -> int:
    content_index = len(cacheable_texts)
    cacheable_texts.append(
        {"text": text, "is_md": True, "metadata": metadata}
    )
    return content_index


def _relation_evidence(
    edge: dict[str, Any],
    node_labels: dict[str, str],
) -> str:
    source_label = node_labels.get(edge["source_node_id"], edge["source_node_id"])
    target_label = node_labels.get(edge["target_node_id"], edge["target_node_id"])
    relation = edge["relation_type"]
    if edge.get("predicate"):
        relation = f"{relation} ({edge['predicate']})"

    statement = f"{source_label} --{relation}--> {target_label}"
    quotes = tuple(dict.fromkeys(edge["evidence_quotes"]))
    if not quotes:
        return statement
    evidence = "\n".join(
        f"{index}. {quote}" for index, quote in enumerate(quotes, start=1)
    )
    return f"{statement}\nEvidence:\n{evidence}"


def _preview(text: str) -> str:
    preview = text.strip()
    if len(preview) <= _SOURCE_PREVIEW_CHARS:
        return preview
    return f"{preview[:_SOURCE_PREVIEW_CHARS].rstrip()}..."

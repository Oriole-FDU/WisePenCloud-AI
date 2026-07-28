from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from neo4j_graphrag.experimental.components.types import Neo4jGraph


# 缓存中的统一节点 ID 前缀。
# SDK 候选图中的节点 ID 形如 "chunk_id:UUID"，直接缓存会绑定到具体 chunk；
# 替换为统一前缀后，同一段窗口文本的抽取结果可以在 chunk_id 变化后继续命中缓存。
_CACHED_NODE_PREFIX = "cached:"


def slice_window_graph(graph: Neo4jGraph, chunk_id: str) -> Neo4jGraph:
    """提取属于指定 chunk 的子图。"""
    # 节点 ID 以 "chunk_id:" 开头作为窗口归属的强约束，避免误把其他窗口的节点带入。
    prefix = f"{chunk_id}:"
    nodes = tuple(node for node in graph.nodes if node.id.startswith(prefix))
    node_ids = {node.id for node in nodes}

    # 关系端点必须都落在当前窗口的节点集合内，悬挂的端点直接丢弃。
    return Neo4jGraph(
        nodes=list(nodes),
        relationships=[
            relation
            for relation in graph.relationships
            if relation.start_node_id in node_ids and relation.end_node_id in node_ids
        ],
    )


def encode_cached_graph(graph: Neo4jGraph, chunk_id: str) -> str:
    """将 chunk 绑定的图转换为可复用缓存格式。"""
    prefix = f"{chunk_id}:"

    normalized = Neo4jGraph(
        nodes=[
            node.model_copy(
                update={"id": _replace_node_prefix(node.id, prefix, _CACHED_NODE_PREFIX)}
            )
            for node in graph.nodes
        ],
        relationships=[
            relation.model_copy(
                update={
                    "start_node_id": _replace_node_prefix(relation.start_node_id, prefix, _CACHED_NODE_PREFIX),
                    "end_node_id": _replace_node_prefix(relation.end_node_id, prefix, _CACHED_NODE_PREFIX),
                }
            )
            for relation in graph.relationships
        ],
    )

    return normalized.model_dump_json()


def decode_cached_graph(payload: str | None, chunk_id: str) -> Neo4jGraph | None:
    """从缓存恢复当前 chunk 对应的图。"""
    if payload is None:
        return None

    try:
        graph = Neo4jGraph.model_validate_json(payload)
    except ValueError:
        return None

    prefix = f"{chunk_id}:"
    try:
        return Neo4jGraph(
            nodes=[
                node.model_copy(
                    update={"id": _replace_node_prefix(node.id, _CACHED_NODE_PREFIX, prefix)}
                )
                for node in graph.nodes
            ],
            relationships=[
                relation.model_copy(
                    update={
                        "start_node_id": _replace_node_prefix(
                            relation.start_node_id, _CACHED_NODE_PREFIX, prefix
                        ),
                        "end_node_id": _replace_node_prefix(
                            relation.end_node_id, _CACHED_NODE_PREFIX, prefix
                        ),
                    }
                )
                for relation in graph.relationships
            ],
        )
    except ValueError:
        return None


def _replace_node_prefix(value: str, old: str, new: str) -> str:
    """替换节点 ID 前缀，并校验来源是否合法。"""
    if not value.startswith(old):
        raise ValueError("graph node id does not match extraction window")
    return f"{new}{value[len(old):]}"

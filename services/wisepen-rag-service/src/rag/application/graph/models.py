"""图用例的公共拓扑与逐来源描述；仅投影已授权事实，不改写核心模型。"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from rag.core.schema import GraphEdge, GraphNode


@dataclass(frozen=True, slots=True)
class GraphEntitySource:
    """一个已授权 Chunk 对实体的描述，不拼接或摘要其他来源。"""

    chunk_id: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class GraphEntity:
    """一个逻辑实体只返回一次；不重复名称、本体、类别和本体允许提示。"""

    id: str
    sources: list[GraphEntitySource] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class GraphRelationshipSource:
    """关系的来源相关字段，关键词和 metadata 不跨来源合并。"""

    chunk_id: str
    description: str | None = None
    keywords: list[str] | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GraphRelationship:
    """以完整关系 ID 标识有向谓词事实，来源字段独立保留。"""

    id: str
    sources: list[GraphRelationshipSource] = field(default_factory=list)


def group_entities(nodes: Iterable[GraphNode]) -> list[GraphEntity]:
    """按逻辑 ID 收拢实体，实体及来源均按首次出现排序，同来源只保留一次。"""
    grouped: dict[str, dict[str, GraphNode]] = {}
    for node in nodes:
        grouped.setdefault(node.id, {}).setdefault(node.source_id, node)
    return [
        GraphEntity(
            id,
            [
                GraphEntitySource(node.source_id, node.description)
                for node in sources.values()
            ],
        )
        for id, sources in grouped.items()
    ]


def group_relationships(edges: Iterable[GraphEdge]) -> list[GraphRelationship]:
    """只收拢相同有向谓词身份；每份事实已经由读取边界确认单一 Chunk 来源。"""
    grouped: dict[str, dict[str, GraphEdge]] = {}
    for edge in edges:
        grouped.setdefault(edge.id, {}).setdefault(edge.source_ids[0], edge)

    return [
        GraphRelationship(
            id,
            [
                GraphRelationshipSource(
                    chunk_id, edge.description, edge.keywords, edge.metadata
                )
                for chunk_id, edge in sources.items()
            ],
        )
        for id, sources in grouped.items()
    ]

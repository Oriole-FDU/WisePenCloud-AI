"""RAG 框架共享的内容事实、图模型与本体。"""

from .content import Chunk, ContentRevision, Document, DocumentStructure
from .graph import (
    EntitySpec,
    GraphDirection,
    GraphEdge,
    GraphNode,
    GraphRelationOption,
    Ontology,
    RelationSpec,
    parse_graph_facts,
)

__all__ = [
    "Chunk",
    "ContentRevision",
    "Document",
    "DocumentStructure",
    "EntitySpec",
    "GraphDirection",
    "GraphEdge",
    "GraphNode",
    "GraphRelationOption",
    "Ontology",
    "RelationSpec",
    "parse_graph_facts",
]

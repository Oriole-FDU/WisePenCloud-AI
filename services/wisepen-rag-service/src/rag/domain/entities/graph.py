"""Mongo 中的图谱事实投影。"""

from typing import ClassVar, Literal

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from rag.application.graph.models import GraphEdge, GraphNode


class GraphNodeProjectionEntity(Document):
    """一个节点在一份资源 revision 的来源投影。"""

    node: GraphNode
    resource_id: str
    content_revision: str
    source_ids: list[str] = Field(default_factory=list)
    producer_id: str | None = None
    filter_values: dict[str, str | int | float | bool] = Field(default_factory=dict)

    class Settings:
        name = "graph_node_projections"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("resource_id", ASCENDING), ("content_revision", ASCENDING), ("node.node_id", ASCENDING), ("producer_id", ASCENDING)],
                name="graph_node_projection_source_unique",
                unique=True,
            ),
        ]


class GraphEdgeProjectionEntity(Document):
    """一条边在一份资源 revision 的来源投影。"""

    edge: GraphEdge
    resource_id: str
    content_revision: str
    source_ids: list[str] = Field(default_factory=list)
    producer_id: str | None = None
    filter_values: dict[str, str | int | float | bool] = Field(default_factory=dict)

    class Settings:
        name = "graph_edge_projections"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("resource_id", ASCENDING), ("content_revision", ASCENDING), ("edge.edge_id", ASCENDING), ("producer_id", ASCENDING)],
                name="graph_edge_projection_source_unique",
                unique=True,
            ),
        ]


class GraphChunkSourceEntity(Document):
    """图元回查所属 Chunk 的来源记录。"""

    source_id: str
    target_type: Literal["node", "edge"]
    target_id: str
    resource_id: str
    content_revision: str
    section_id: str | None = None
    chunk_id: str

    class Settings:
        name = "graph_chunk_sources"
        indexes: ClassVar[list[IndexModel]] = [
            IndexModel(
                [("source_id", ASCENDING)],
                name="graph_chunk_source_unique",
                unique=True,
            ),
            IndexModel(
                [("resource_id", ASCENDING), ("content_revision", ASCENDING), ("chunk_id", ASCENDING)],
                name="graph_chunk_source_revision_chunk",
            ),
        ]

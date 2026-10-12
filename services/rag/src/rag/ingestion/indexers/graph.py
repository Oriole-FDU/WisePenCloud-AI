"""解析已保存的来源图事实，校验后只写可重建拓扑，不修改 Chunk。"""

from common.logger import warning
from pydantic import TypeAdapter

from rag.core.schema import Chunk, GraphEdge, GraphNode, Ontology
from rag.storage.graph import GraphSourceProjection, GraphWriter

from .base import Indexer

_node_adapter = TypeAdapter(GraphNode)
_edge_adapter = TypeAdapter(GraphEdge)


class GraphIndexer(Indexer):
    """只读输入；全部解析完成后才进入 Writer，来源损坏不能当空成功。"""

    def __init__(self, *, ontology: Ontology, writer: GraphWriter) -> None:
        self._ontology = ontology  # 本体校验器
        self._writer = writer  # 拓扑写入器

    async def index(self, chunks: list[Chunk]) -> None:
        projections = []
        for chunk in chunks:
            entities = chunk.metadata.get("entities")
            relationships = chunk.metadata.get("relationships")

            # 事实字段必须是列表，否则视为来源损坏
            if not isinstance(entities, list) or not isinstance(relationships, list):
                raise TypeError(
                    "graph facts must contain entity and relationship lists"
                )

            nodes: dict[str, GraphNode] = {}
            edges: dict[str, GraphEdge] = {}

            # 逐条解析并校验实体，被拒的只警告不中断
            for value in entities:
                node = _node_adapter.validate_python(value, extra="forbid")
                if node.source_id != chunk.chunk_id:
                    raise ValueError("graph entity source does not match its chunk")
                try:
                    self._ontology.validate_node(node)
                except ValueError as exc:
                    warning(
                        "graph_index_entity_rejected",
                        chunk_id=chunk.chunk_id,
                        error=str(exc),
                    )
                    continue
                nodes.setdefault(node.id, node)  # 同 ID 去重，保留首个

            # 逐条解析并校验关系，被拒的只警告不中断
            for value in relationships:
                edge = _edge_adapter.validate_python(value, extra="forbid")
                if edge.source_ids != [chunk.chunk_id]:
                    raise ValueError(
                        "graph relationship sources do not match its chunk"
                    )
                try:
                    self._ontology.validate_edge(edge, nodes)
                except ValueError as exc:
                    warning(
                        "graph_index_relationship_rejected",
                        chunk_id=chunk.chunk_id,
                        error=str(exc),
                    )
                    continue
                edges.setdefault(edge.id, edge)  # 同 ID 去重，保留首个

            # 组装来源投影：来源元数据 + 通过校验的节点与边
            projections.append(
                GraphSourceProjection(
                    chunk_id=chunk.chunk_id,
                    metadata={
                        "resource_id": chunk.resource_id,
                        "content_revision": chunk.content_revision,
                    },
                    nodes=list(nodes.values()),
                    edges=list(edges.values()),
                )
            )

        # 全部解析成功后再统一写入，避免半途损坏污染存储
        if projections:
            await self._writer.replace_sources(projections)

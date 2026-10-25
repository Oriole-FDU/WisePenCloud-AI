"""显式逻辑实体种子的一跳拓扑读取；后续扩展由调用方重新选择种子。"""

from dataclasses import dataclass, field

from rag.application.common.snapshot import ResourceSnapshot, ResourceSnapshotReader
from rag.core.schema.filters import MetadataFilters
from rag.storage.graph import GraphDirection, GraphReader
from .graph_facts import GraphFactLoader
from .models import GraphEntity, GraphRelationship, group_entities, group_relationships


@dataclass(frozen=True, slots=True)
class GraphTraversalResult:
    """一次一跳调用的共享拓扑；每个逻辑对象下保留各来源，不附带 Chunk 原文。"""

    entities: list[GraphEntity] = field(default_factory=list)
    relationships: list[GraphRelationship] = field(default_factory=list)


class GraphTraversal:
    """固定读取依赖，不调用模型，也不信任未回查的逻辑 ID 或投影事实。"""

    def __init__(
            self,
            *,
            snapshot_reader: ResourceSnapshotReader,
            graph_reader: GraphReader,
    ) -> None:
        self._snapshot_reader = snapshot_reader  # 回查来源授权与当前事实。
        self._graph_reader = graph_reader  # 定位种子和一跳关系。

    async def traverse(
            self,
            node_ids: list[str],
            *,
            snapshot: ResourceSnapshot,
            direction: GraphDirection = GraphDirection.BOTH,
            relation_types: list[str] | None = None,
            relation_limit: int = 100,
            filters: MetadataFilters | None = None,
    ) -> GraphTraversalResult:
        """至少一个可见来源确认种子后才读一跳；不暴露任意多跳入口。"""
        # 种子和预算由调用方保证；这里只把方向归一为枚举。
        resolved_direction = GraphDirection(direction)

        # 空种子直接短路
        if not node_ids:
            return GraphTraversalResult()

        loader = GraphFactLoader(
            snapshot_reader=self._snapshot_reader,
            graph_reader=self._graph_reader,
            snapshot=snapshot,
            filters=filters,
        )

        # 先确认种子；未知或不可见 ID 不得借邻域中其他来源成为可遍历节点。
        nodes = {
            (node.id, node.source_id): node
            for node in await loader.read_nodes(list(dict.fromkeys(node_ids)))
        }
        if not nodes or relation_types == []:
            return GraphTraversalResult(group_entities(nodes.values()))

        projections = await self._graph_reader.get_neighbors(
            list(dict.fromkeys(node.id for node in nodes.values())),
            direction=resolved_direction,
            relation_types=relation_types,
            filters=filters,
            limit=relation_limit,
        )
        edges = {}
        for edge in await loader.read_edges(projections):
            chunk_id = edge.source_ids[0]
            edges.setdefault((edge.id, chunk_id), edge)
            # 返回实际关系端点的同来源事实；不额外读取邻居的其他描述或第二跳。
            source_nodes = loader.facts[chunk_id][0]
            for key in (edge.source_id, edge.target_id):
                node = source_nodes[key]
                nodes.setdefault((node.id, node.source_id), node)

        return GraphTraversalResult(
            group_entities(nodes.values()), group_relationships(edges.values())
        )

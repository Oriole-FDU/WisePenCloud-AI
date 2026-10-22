"""按请求快照读取当前图事实；图库只定位来源，不替代 Mongo 授权与事实。"""

from rag.application.common.snapshot import ResourceSnapshot, ResourceSnapshotReader
from rag.core.schema import (
    Chunk,
    GraphEdge,
    GraphNode,
    GraphRelationOption,
    parse_graph_facts,
)
from rag.core.schema.filters import FilterCondition, FilterOperator, MetadataFilters
from rag.storage.graph import GraphDirection, GraphReader


class GraphFactLoader:
    """一次调用内共享来源读取与解析；筛选条件不承担授权职责。"""

    def __init__(
        self,
        *,
        snapshot_reader: ResourceSnapshotReader,
        graph_reader: GraphReader,
        snapshot: ResourceSnapshot,
        filters: MetadataFilters | None = None,
    ) -> None:
        self.snapshot_reader = snapshot_reader
        self.graph_reader = graph_reader
        self.snapshot = snapshot
        self.filters = filters
        self.chunks: dict[str, Chunk] = {}
        self.facts: dict[
            str, tuple[dict[str, GraphNode], dict[str, GraphEdge]]
        ] = {}
        self._observed: set[str] = set()

    async def load_chunks(self, chunk_ids: list[str]) -> None:
        """来源分批授权；缺失和不可见来源在本次调用内也只尝试一次。"""
        # 本次调用内已观察过的来源不再重复请求。
        pending = [
            key
            for key in dict.fromkeys(chunk_ids)
            if key not in self._observed
        ]
        for start in range(0, len(pending), 64):
            batch = pending[start : start + 64]
            chunks = await self.snapshot_reader.read_chunks(
                batch, snapshot=self.snapshot
            )
            for chunk in chunks:
                # 无图事实的 Chunk 不进入缓存。
                if (
                    "entities" not in chunk.metadata
                    and "relationships" not in chunk.metadata
                ):
                    continue

                nodes, edges = parse_graph_facts(chunk)
                if self.filters is not None:
                    # 本体取当前事实；关系及其同来源两端都必须满足筛选。
                    nodes = {
                        key: node
                        for key, node in nodes.items()
                        if _matches_source(
                            {
                                "resource_id": chunk.resource_id,
                                "content_revision": chunk.content_revision,
                                "ontology_name": node.ontology_name,
                            },
                            self.filters,
                        )
                    }
                    edges = {
                        key: edge
                        for key, edge in edges.items()
                        if edge.source_id in nodes and edge.target_id in nodes
                    }

                self.facts[chunk.chunk_id] = nodes, edges
                self.chunks[chunk.chunk_id] = chunk
            self._observed.update(batch)

    async def read_nodes(self, node_ids: list[str]) -> list[GraphNode]:
        # 图库只提供节点投影（ID 与来源），内容以 Mongo 事实为准。
        projections = await self.graph_reader.get_nodes(
            node_ids, filters=self.filters
        )
        await self.load_chunks([node.source_id for node in projections])

        nodes = []
        for projection in projections:
            facts = self.facts.get(projection.source_id)
            node = facts[0].get(projection.id) if facts is not None else None
            if node is not None:
                nodes.append(node)
        return nodes

    async def read_edges(self, projections: list[GraphEdge]) -> list[GraphEdge]:
        """投影只提供关系 ID 与来源；描述等内容始终取当前 Mongo 事实。"""
        await self.load_chunks([edge.source_ids[0] for edge in projections])

        edges = []
        for projection in projections:
            facts = self.facts.get(projection.source_ids[0])
            edge = facts[1].get(projection.id) if facts is not None else None
            if edge is not None:
                edges.append(edge)
        return edges

    async def visible_connections(
        self, node_ids: list[str]
    ) -> dict[str, list[GraphRelationOption]]:
        """完整定位一跳后再授权，不能用检索数量上限推导连接不存在。"""
        # 按节点收集其一跳关系的方向集合。
        directions: dict[str, dict[str, GraphDirection]] = {
            key: {} for key in dict.fromkeys(node_ids)
        }
        if not directions:
            return {}

        # 先完整定位一跳（limit=None），再授权，避免数量上限导致漏判。
        projections = await self.graph_reader.get_neighbors(
            list(directions),
            direction=GraphDirection.BOTH,
            filters=self.filters,
            limit=None,
        )
        for edge in await self.read_edges(projections):
            for node_id, direction in (
                (edge.source_id, GraphDirection.OUTGOING),
                (edge.target_id, GraphDirection.INCOMING),
            ):
                if node_id in directions:
                    previous = directions[node_id].get(edge.relation_type)
                    # 同关系类型出现双向时标记为 BOTH。
                    directions[node_id][edge.relation_type] = (
                        GraphDirection.BOTH
                        if previous is not None and previous != direction
                        else direction
                    )

        # 输出按关系类型排序，便于调用方稳定消费。
        return {
            key: [
                GraphRelationOption(kind, values[kind])
                for kind in sorted(values)
            ]
            for key, values in directions.items()
        }


def _matches_source(
    metadata: dict[str, str | None], filters: MetadataFilters
) -> bool:
    """当前事实复核与存储边界相同的来源筛选语义。"""
    matches = []
    for item in filters.filters:
        if isinstance(item, MetadataFilters):
            # 嵌套条件递归求值。
            matches.append(_matches_source(metadata, item))
        else:
            value = metadata[item.key]
            matches.append(
                value == item.value
                if item.operator is FilterOperator.EQ
                else value in item.value
            )

    if filters.condition is FilterCondition.NOT:
        return not matches[0]
    if filters.condition is FilterCondition.OR:
        return any(matches)
    return all(matches)
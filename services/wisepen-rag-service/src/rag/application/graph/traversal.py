"""显式逻辑实体种子的一跳拓扑读取；后续扩展由调用方重新选择种子。"""

from rag.application.snapshot import ResourceSnapshot, ResourceSnapshotReader
from rag.core.schema.filters import MetadataFilters
from rag.storage.graph import GraphDirection, GraphReader, GraphVectorKind

from .evidence import (
    EvidenceBuilder,
    GraphEvidence,
    GraphEvidenceLoader,
    validate_graph_options,
)


class GraphTraversal:
    """固定读取依赖，不调用模型，也不信任未回查的逻辑 ID 或投影事实。"""

    def __init__(
        self,
        *,
        snapshot_reader: ResourceSnapshotReader,
        graph_reader: GraphReader,
    ) -> None:
        self._snapshot_reader = snapshot_reader  # 读取 Chunk 原文
        self._graph_reader = graph_reader        # 读取拓扑投影

    async def traverse(
        self,
        node_ids: list[str],
        *,
        snapshot: ResourceSnapshot,
        direction: GraphDirection | None = None,
        relation_types: list[str] | None = None,
        relation_limit: int = 100,
        filters: MetadataFilters | None = None,
    ) -> list[GraphEvidence]:
        """至少一个可见来源确认种子后才读一跳；不暴露任意多跳入口。"""

        # 逻辑 ID 必须非空
        if any(not key.strip() for key in node_ids):
            raise ValueError("node_ids must contain nonempty logical IDs")

        # 归一化并校验扩展参数
        resolved_direction = validate_graph_options(
            direction=direction,
            relation_types=relation_types,
            relation_limit=relation_limit,
        )

        # 空种子直接短路
        if not node_ids:
            return []

        loader = GraphEvidenceLoader(
            snapshot_reader=self._snapshot_reader,
            graph_reader=self._graph_reader,
            snapshot=snapshot,
            filters=filters,
        )

        # 为每个去重后的种子建立独立证据组
        groups = {
            (GraphVectorKind.ENTITY, key): EvidenceBuilder(GraphVectorKind.ENTITY, key)
            for key in dict.fromkeys(node_ids)
        }

        # 先用当前 Mongo 事实确认种子，再读一跳
        await loader.read_seeds(groups)

        # 只保留确实被来源确认的实体，再扩展邻域
        groups = {key: group for key, group in groups.items() if group.nodes}
        await loader.expand_entities(
            groups,
            direction=resolved_direction,
            relation_types=relation_types,
            relation_limit=relation_limit,
        )

        return [group.build() for group in groups.values()]
"""两个图用例共享的来源核验和证据组装，不把拓扑投影当作权威事实。"""

from dataclasses import dataclass, field

from rag.application.snapshot import ResourceSnapshot, ResourceSnapshotReader
from rag.core.schema import (
    Chunk,
    GraphEdge,
    GraphNode,
    GraphRelationOption,
    parse_graph_facts,
)
from rag.core.schema.filters import (
    FilterCondition,
    FilterOperator,
    MetadataFilters,
)
from rag.storage.graph import GraphDirection, GraphReader, GraphVectorKind


@dataclass(frozen=True, slots=True)
class GraphSourceChunk:
    """证据来源的完整原文；不携带生成上下文或任意业务 metadata。"""

    chunk_id: str
    resource_id: str
    content_revision: str
    section_id: str | None
    section_path: list[str]
    raw_text: str


@dataclass(frozen=True, slots=True)
class GraphEntity:
    """实体读取视图；身份由逻辑 ID 表达，来源描述和类别关系提示独立保留。"""

    id: str
    source_id: str
    description: str | None
    relations: list[GraphRelationOption]  # 允许的关系，不宣称可见事实中存在。


@dataclass(frozen=True, slots=True)
class GraphEvidence:
    """一个逻辑种子的证据组；每条事实保留其 Chunk 来源，不汇总描述。"""

    kind: GraphVectorKind
    fact_id: str  # 核心逻辑 ID，不是来源向量记录 ID。
    entities: list[GraphEntity]
    relationships: list[GraphEdge]
    sources: list[GraphSourceChunk]


@dataclass(frozen=True, slots=True)
class GraphHit:
    """完成 Pipeline 排序后的图命中；分数沿用最终排序阶段语义。"""

    evidence: GraphEvidence
    rank: int
    score: float


def validate_graph_options(
    *,
    direction: GraphDirection | None,
    relation_types: list[str] | None,
    relation_limit: int,
) -> GraphDirection:
    """用例输入边界统一校验。"""

    # 未指定方向时默认双向
    resolved = GraphDirection.BOTH if direction is None else GraphDirection(direction)

    if relation_limit <= 0:
        raise ValueError("relation limit must be a positive integer")
    if relation_types is not None and any(
        not value.strip() for value in relation_types
    ):
        raise ValueError("relation types must not be blank")
    return resolved


def _matches_source(chunk: Chunk, filters: MetadataFilters) -> bool:
    """在 Chunk 上就地求值 MetadataFilters，支持 NOT / OR / AND 三种组合。"""
    matches = []
    for item in filters.filters:
        if isinstance(item, MetadataFilters):
            # 嵌套组递归求值
            matches.append(_matches_source(chunk, item))
        else:
            value = getattr(chunk, item.key)
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


@dataclass(slots=True)
class EvidenceBuilder:
    """调用内按来源去重；每个种子各持有自己的材料集合。"""

    kind: GraphVectorKind
    fact_id: str
    nodes: dict[tuple[str, str], GraphNode] = field(default_factory=dict)
    edges: dict[tuple[str, str], GraphEdge] = field(default_factory=dict)
    chunks: dict[str, Chunk] = field(default_factory=dict)

    def add_node(self, node: GraphNode, chunk: Chunk) -> None:
        # 用 (节点 ID, 来源) 去重，不同来源的同一逻辑节点保留
        self.nodes.setdefault((node.id, node.source_id), node)
        self.chunks.setdefault(chunk.chunk_id, chunk)

    def add_edge(
        self, edge: GraphEdge, nodes: dict[str, GraphNode], chunk: Chunk
    ) -> None:
        # 端点必须来自支持当前关系的同一 Chunk，不补读其他文档的端点描述。
        self.add_node(nodes[edge.source_id], chunk)
        self.add_node(nodes[edge.target_id], chunk)
        self.edges.setdefault((edge.id, chunk.chunk_id), edge)

    def build(self) -> GraphEvidence:
        # 汇总节点、边与来源原文为不可变证据
        return GraphEvidence(
            kind=self.kind,
            fact_id=self.fact_id,
            entities=[
                GraphEntity(node.id, node.source_id, node.description, node.relations)
                for node in self.nodes.values()
            ],
            relationships=list(self.edges.values()),
            sources=[
                GraphSourceChunk(
                    chunk_id=chunk.chunk_id,
                    resource_id=chunk.resource_id,
                    content_revision=chunk.content_revision,
                    section_id=chunk.section_id,
                    section_path=chunk.section_path,
                    raw_text=chunk.raw_text,
                )
                for chunk in self.chunks.values()
            ],
        )


class GraphEvidenceLoader:
    """一次用例调用的读取状态；共享来源读取，证据集合仍按种子隔离。"""

    def __init__(
        self,
        *,
        snapshot_reader: ResourceSnapshotReader,
        graph_reader: GraphReader,
        snapshot: ResourceSnapshot,
        filters: MetadataFilters | None,
    ) -> None:
        self.snapshot_reader = snapshot_reader
        self.graph_reader = graph_reader
        self.snapshot = snapshot
        self.filters = filters
        self.chunks: dict[str, Chunk] = {}                # 本调用内已加载的 Chunk
        self.facts: dict[str, tuple[dict[str, GraphNode], dict[str, GraphEdge]]] = {}
        self._observed: set[str] = set()                  # 已尝试过的 chunk ID

    async def load_chunks(self, chunk_ids: list[str]) -> None:
        """批量加载 Chunk；已在本次调用中出现过的 ID 不重复读取。"""
        pending = [key for key in dict.fromkeys(chunk_ids) if key not in self._observed]
        if not pending:
            return

        chunks = await self.snapshot_reader.read_chunks(pending, snapshot=self.snapshot)
        for chunk in chunks:
            # filters 不匹配则整块跳过
            if self.filters is not None and not _matches_source(chunk, self.filters):
                continue
            # 两个字段均未出现表示尚无图事实；部分缺失或损坏仍由本体解析报错。
            if (
                "entities" not in chunk.metadata
                and "relationships" not in chunk.metadata
            ):
                continue
            self.facts[chunk.chunk_id] = parse_graph_facts(chunk)
            self.chunks[chunk.chunk_id] = chunk

        self._observed.update(pending)  # 不可见/缺失也只读取一次，不是跨请求缓存。

    async def read_seeds(
        self, groups: dict[tuple[GraphVectorKind, str], EvidenceBuilder]
    ) -> None:
        """读回种子自身的来源；向量确认的合法来源即使缺少拓扑也仍保留。"""
        node_ids = [key for kind, key in groups if kind is GraphVectorKind.ENTITY]
        edge_ids = [key for kind, key in groups if kind is GraphVectorKind.RELATIONSHIP]

        # 实体种子：按投影定位当前事实，匹配上才写入证据
        if node_ids:
            projections = await self.graph_reader.get_nodes(
                node_ids, filters=self.filters
            )
            await self.load_chunks([node.source_id for node in projections])
            for projection in projections:
                group = groups[(GraphVectorKind.ENTITY, projection.id)]
                facts = self.facts.get(projection.source_id)
                node = facts[0].get(projection.id) if facts is not None else None
                if node is not None:
                    group.add_node(node, self.chunks[node.source_id])

        # 关系种子：同样以当前 Mongo 事实为准
        if edge_ids:
            projections = await self.graph_reader.get_relations(
                edge_ids, filters=self.filters
            )
            await self.load_chunks([edge.source_ids[0] for edge in projections])
            for projection in projections:
                self._add_current_edge(
                    projection, groups[(GraphVectorKind.RELATIONSHIP, projection.id)]
                )

    def _add_current_edge(self, projection: GraphEdge, group: EvidenceBuilder) -> None:
        # GraphReader 已保证单一来源及拓扑身份；这里只核对当前 Mongo 事实。
        chunk_id = projection.source_ids[0]
        facts = self.facts.get(chunk_id)
        edge = facts[1].get(projection.id) if facts is not None else None
        if edge is not None:
            group.add_edge(edge, facts[0], self.chunks[chunk_id])

    async def expand_entities(
        self,
        groups: dict[tuple[GraphVectorKind, str], EvidenceBuilder],
        *,
        direction: GraphDirection,
        relation_types: list[str] | None,
        relation_limit: int,
    ) -> None:
        """只扩展已确认实体；global 关系组不消费这次邻域的材料。"""
        # 仅挑出已含节点的实体组参与拓扑扩展
        entity_groups = {
            key: group
            for (kind, key), group in groups.items()
            if kind is GraphVectorKind.ENTITY and group.nodes
        }
        if not entity_groups or relation_types == []:
            return

        projections = await self.graph_reader.get_neighbors(
            list(entity_groups),
            direction=direction,
            relation_types=relation_types,
            filters=self.filters,
            limit=relation_limit,
        )
        await self.load_chunks([edge.source_ids[0] for edge in projections])

        for projection in projections:
            # 按方向取出需要归属的端点
            endpoints = []
            if direction in (GraphDirection.OUTGOING, GraphDirection.BOTH):
                endpoints.append(projection.source_id)
            if direction in (GraphDirection.INCOMING, GraphDirection.BOTH):
                endpoints.append(projection.target_id)

            # 端点属于已确认实体时，把这条边挂到对应实体组
            for key in dict.fromkeys(endpoints):
                group = entity_groups.get(key)
                if group is not None:
                    self._add_current_edge(projection, group)
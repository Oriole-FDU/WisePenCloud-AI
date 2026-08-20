"""从 navigation state 的已知节点扩展有 ReadingBlock 证据的知识路径。"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from common.utils.ranking import (
    RankCandidate,
    RankingPipeline,
    RankQuery,
    RankRequest,
)

from rag.application.rag.acl import PermissionAuthorizer
from rag.application.rag.navigate.evidence_verifiers import GraphEvidenceVerifier
from rag.application.rag.navigate.reading_blocks import (
    ReadingBlockSectionView,
    present_reading_block,
)
from rag.application.rag.read.content import format_page_range
from rag.domain.models.acl import PermissionScope
from rag.domain.models.graph import (
    KnowledgeEntityType,
    KnowledgeNode,
    KnowledgeNodeKind,
    KnowledgeRelationType,
    TraversalDirection,
)
from rag.domain.repositories.mongo.published_resource_reader import (
    PublishedGraphEvidence,
)
from rag.domain.repositories.neo4j.knowledge_graph_repository import (
    KnowledgeGraphRepository,
    TraversedEdge,
    TraversedPath,
)
from rag.domain.repositories.redis.navigation_state_store import (
    NavigationStateMissingError,
    NavigationStateStore,
)


class UnknownSeedNodeError(RuntimeError):
    """请求的 seed 尚未被当前 navigation state 发现。"""


class GraphAccessRevokedError(RuntimeError):
    """图谱展开期间证据所属资源失去可读权限。"""


class NavigationStateNotFoundError(RuntimeError):
    """导航状态不存在，或不属于当前用户与会话。"""


class GraphNodeRole(StrEnum):
    """公开路径节点的导航角色。"""

    SEED = "seed"
    DISCOVERED = "discovered"


@dataclass(slots=True)
class GraphReadingBlockView:
    """EXPAND 证据对应的完整 ReadingBlock，不携带检索命中字段。"""

    resource_id: str
    reading_block_id: str
    text: str
    sections: list[ReadingBlockSectionView] = field(default_factory=list)
    page_range: str | None = None
    anchor_labels: list[str] = field(default_factory=list)

@dataclass(slots=True)
class GraphEvidenceRefView:
    """关系或节点提及到完整 ReadingBlock 的公开证据。"""

    resource_id: str
    reading_block_id: str
    quote: str


@dataclass(slots=True)
class GraphNodeView:
    """公开节点字段；role 只标记 seed 或本次 discovered 节点。"""

    node_id: str
    label: str
    kind: KnowledgeNodeKind
    entity_type: KnowledgeEntityType | None = None
    role: GraphNodeRole | None = None


@dataclass(slots=True)
class DiscoveredKnowledgeNodeView:
    """本次原子写入 state 的新节点及其提及证据。"""

    node_id: str
    label: str
    kind: KnowledgeNodeKind
    entity_type: KnowledgeEntityType | None = None
    role: GraphNodeRole = GraphNodeRole.DISCOVERED
    mention_evidence: list[GraphEvidenceRefView] = field(default_factory=list)


@dataclass(slots=True)
class GraphRelationEndpointView:
    """路径关系端点；已有 state 节点也只在关系中出现，不单独公开集合。"""

    node_id: str
    label: str


@dataclass(slots=True)
class GraphRelationView:
    """按知识事实方向表达的关系及其证据。"""

    source: GraphRelationEndpointView
    predicate: str
    target: GraphRelationEndpointView
    relation_evidence: list[GraphEvidenceRefView] = field(default_factory=list)


@dataclass(slots=True)
class GraphPathView:
    """按实际遍历顺序渲染的自然路径和精确关系记录。"""

    path: str
    relations: list[GraphRelationView] = field(default_factory=list)


@dataclass(slots=True)
class GraphExpandResult:
    state_id: str
    traversal_direction: TraversalDirection = TraversalDirection.BOTH
    seed_nodes: list[GraphNodeView] = field(default_factory=list)
    discovered_nodes: list[DiscoveredKnowledgeNodeView] = field(default_factory=list)
    paths: list[GraphPathView] = field(default_factory=list)
    evidence_reading_blocks: list[GraphReadingBlockView] = field(default_factory=list)


@dataclass(slots=True)
class _EligiblePath:
    """关系与新节点证据都已核验、等待原子 state 竞争的路径。"""

    path: TraversedPath
    node_evidence: dict[str, list[PublishedGraphEvidence]] = field(
        default_factory=dict
    )


class KnowledgeGraphExpander:
    """编排知识关系遍历、证据核验、路径排序和状态原子扩展。"""

    def __init__(
        self,
        *,
        knowledge_graph: KnowledgeGraphRepository,
        ranking_pipeline: RankingPipeline,
        evidence_verifier: GraphEvidenceVerifier,
        authorizer: PermissionAuthorizer,
        state_store: NavigationStateStore,
    ) -> None:
        self._knowledge_graph = knowledge_graph
        self._ranking_pipeline = ranking_pipeline
        self._evidence_verifier = evidence_verifier
        self._authorizer = authorizer
        self._state_store = state_store

    async def expand(
        self,
        *,
        state_id: str,
        session_id: str,
        permission_scope: PermissionScope,
        seed_node_ids: list[str],
        query: str,
        relation_types: list[KnowledgeRelationType] | None = None,
        direction: TraversalDirection = TraversalDirection.BOTH,
        max_depth: int = 1,
        max_results: int = 10,
    ) -> GraphExpandResult:
        effective_query = query.strip()
        if not effective_query:
            raise ValueError("expand query must not be empty")

        # 加载导航状态，校验用户和会话归属
        state = await self._state_store.get(state_id)
        if (
            state is None
            or state.user_id != permission_scope.user_id
            or state.session_id != session_id
        ):
            raise NavigationStateNotFoundError(state_id)

        # 校验 seed 节点必须已在 state 中（LOCATE 阶段已发现）
        known_node_ids = set(state.known_node_ids)
        unknown_seed_ids = [
            node_id
            for node_id in seed_node_ids
            if node_id not in known_node_ids
        ]
        if unknown_seed_ids:
            raise UnknownSeedNodeError(unknown_seed_ids[0])

        # 子图查询：从 seed 节点出发按方向/深度遍历图谱，返回路径和节点 mentions
        requested_path_limit = min(max(max_results * 4, 1), 80)
        subgraph = await self._knowledge_graph.find_subgraph(
            seed_node_ids=seed_node_ids,
            permission_scope=permission_scope,
            relation_types=relation_types or [],
            direction=direction,
            max_depth=max_depth,
            path_limit=requested_path_limit,
            mention_limit_per_node=3,
        )
        paths = subgraph.paths
        # ACL 复查：过滤掉涉及无权限资源的路径
        paths = await self._filter_readable_paths(paths, permission_scope)
        # 收集 seed 节点视图（按请求顺序排列）
        seed_nodes_by_id = {
            node.node_id: node
            for path in paths
            for node in path.nodes
            if node.node_id in seed_node_ids
        }
        seed_nodes = [
            _to_seed_node_view(seed_nodes_by_id[node_id])
            for node_id in seed_node_ids
            if node_id in seed_nodes_by_id
        ]
        # 只保留包含新节点（不在 known 中的）的路径
        paths = [
            path
            for path in paths
            if any(node.node_id not in known_node_ids for node in path.nodes)
        ]
        if not paths:
            return GraphExpandResult(
                state_id=state.state_id,
                traversal_direction=direction,
                seed_nodes=seed_nodes,
            )

        # 按 query 对路径相关性精排
        ranking = await self._ranking_pipeline.arank(
            RankRequest(
                query=RankQuery(text=effective_query),
                candidates=tuple(
                    RankCandidate(
                        candidate_id=str(index),
                        text=_render_path(path)[0],
                        fields={
                            "nodes": "\n".join(node.label for node in path.nodes),
                            "relations": "\n".join(
                                " ".join(
                                    value for value in
                                    (edge.relation_type.value, edge.predicate) if value
                                )
                                for edge in path.edges
                            ),
                        },
                        prior_rank=index + 1,
                    )
                    for index, path in enumerate(paths)
                ),
                top_k=max_results,
                candidate_limit=len(paths),
            )
        )
        paths = [paths[int(item.candidate_id)] for item in ranking.ranked]
        
        # 批量核验每条路径的关系证据
        evidence_by_edge = await self._verify_path_evidence(paths)

        # 批量核验路径中节点的 mention 证据
        # 子图查询已一次性取得 mentions，这里按 ACL 复查后的路径裁剪，再批量回源
        path_node_ids = {
            node.node_id
            for path in paths
            for node in path.nodes
            if node.kind is not KnowledgeNodeKind.RESOURCE
        }
        path_resource_ids = {
            resource_id for path in paths for resource_id in _path_resource_ids(path)
        }
        mentions = [
            mention
            for mention in subgraph.mentions
            if mention.node_id in path_node_ids
            and mention.evidence.resource_id in path_resource_ids
        ]
        verified_mentions = await self._evidence_verifier.verify(
            [mention.evidence for mention in mentions]
        )
        mention_evidence_by_node: dict[str, list[PublishedGraphEvidence]] = {}
        for mention, record in zip(mentions, verified_mentions, strict=True):
            mention_evidence_by_node.setdefault(mention.node_id, []).append(record)

        # 为每条路径的每个新节点收集足够证据
        # 实体节点用 mention 证据，RESOURCE 节点用关系证据（无 mention）
        eligible_paths: list[_EligiblePath] = []
        for path in paths:
            node_evidence = await self._resolve_new_node_evidence(
                path,
                evidence_by_edge,
                known_node_ids,
                mention_evidence_by_node,
            )
            if node_evidence is not None:
                eligible_paths.append(
                    _EligiblePath(path=path, node_evidence=node_evidence)
                )
        if not eligible_paths:
            return GraphExpandResult(
                state_id=state.state_id,
                traversal_direction=direction,
                seed_nodes=seed_nodes,
            )

        # 预写权限校验：所有外部读取必须在 state 写入前完成
        # 一旦节点进入 state，不应因证据读取失败留下半完成结果
        candidate_evidence: list[PublishedGraphEvidence] = []
        for eligible in eligible_paths:
            for edge in eligible.path.edges:
                candidate_evidence.extend(evidence_by_edge[edge.edge_id])
            for records in eligible.node_evidence.values():
                candidate_evidence.extend(records)
        await self._ensure_sources_readable(
            candidate_evidence,
            permission_scope,
        )

        # 原子写入 state：将新节点加入 known_nodes，冲突则整体回滚
        candidate_new_ids = list(
            dict.fromkeys(
                node.node_id
                for eligible in eligible_paths
                for node in eligible.path.nodes
                if node.node_id not in known_node_ids
            )
        )
        try:
            added_node_ids = await self._state_store.add_known_nodes(
                state_id=state.state_id,
                node_ids=candidate_new_ids,
            )
        except NavigationStateMissingError as error:
            raise NavigationStateNotFoundError(state.state_id) from error
        added_node_id_set = set(added_node_ids)
        # 裁剪路径，只保留确实成功写入新节点的路径
        eligible_paths = [
            eligible
            for eligible in eligible_paths
            if any(
                node.node_id in added_node_id_set for node in eligible.path.nodes
            )
        ]
        if not eligible_paths:
            return GraphExpandResult(
                state_id=state.state_id,
                traversal_direction=direction,
                seed_nodes=seed_nodes,
            )

        # 构建视图，组装节点视图、路径视图、证据 Section 视图
        nodes_by_id = {
            node.node_id: node
            for eligible in eligible_paths
            for node in eligible.path.nodes
        }
        node_evidence: dict[str, list[PublishedGraphEvidence]] = {}
        for node_id in added_node_ids:
            records = [
                record
                for eligible in eligible_paths
                for record in eligible.node_evidence.get(node_id, [])
            ]
            node_evidence[node_id] = _deduplicate_evidence(records, limit=3)

        retained_evidence: list[PublishedGraphEvidence] = []
        path_views: list[GraphPathView] = []
        for eligible in eligible_paths:
            view, records = _to_path_view(eligible.path, evidence_by_edge)
            path_views.append(view)
            retained_evidence.extend(records)
        for node_id in added_node_ids:
            retained_evidence.extend(node_evidence[node_id])

        evidence_reading_blocks = _build_graph_reading_block_views(
            _deduplicate_evidence(retained_evidence)
        )
  
        return GraphExpandResult(
            state_id=state.state_id,
            traversal_direction=direction,
            seed_nodes=seed_nodes,
            discovered_nodes=[
                _to_discovered_node_view(
                    nodes_by_id[node_id],
                    node_evidence[node_id],
                )
                for node_id in added_node_ids
            ],
            paths=path_views,
            evidence_reading_blocks=evidence_reading_blocks,
        )

    async def _verify_path_evidence(
        self,
        paths: list[TraversedPath],
    ) -> dict[str, list[PublishedGraphEvidence]]:
        edges = {edge.edge_id: edge for path in paths for edge in path.edges}
        return {
            edge.edge_id: await self._evidence_verifier.verify(edge.evidence)
            for edge in edges.values()
        }

    @staticmethod
    async def _resolve_new_node_evidence(
        path: TraversedPath,
        evidence_by_edge: dict[str, list[PublishedGraphEvidence]],
        known_node_ids: set[str],
        mention_evidence_by_node: dict[str, list[PublishedGraphEvidence]],
    ) -> dict[str, list[PublishedGraphEvidence]] | None:
        new_nodes = [
            node for node in path.nodes if node.node_id not in known_node_ids
        ]
        records_by_node: dict[str, list[PublishedGraphEvidence]] = {}
        for node in new_nodes:
            if node.kind is not KnowledgeNodeKind.RESOURCE:
                records_by_node[node.node_id] = list(
                    mention_evidence_by_node.get(node.node_id, [])
                )

        for node in new_nodes:
            if node.kind is KnowledgeNodeKind.RESOURCE:
                # Resource 根节点没有 MENTION；它的节点证据只能取与其相连且已经
                # 核验的关系证据，仍然保证调用方能读到对应 ReadingBlock。
                records_by_node[node.node_id] = _deduplicate_evidence(
                    [
                        record
                        for edge in path.edges
                        if node.node_id
                        in (edge.source_node_id, edge.target_node_id)
                        for record in evidence_by_edge[edge.edge_id]
                    ],
                    limit=3,
                )
            if not records_by_node.get(node.node_id):
                return None
        return records_by_node

    async def _filter_readable_paths(
        self,
        paths: list[TraversedPath],
        permission_scope: PermissionScope,
    ) -> list[TraversedPath]:
        """Neo4j 查询后以本地 ACL 事实复查路径涉及的全部资源。"""
        readable_resource_ids = set(
            await self._authorizer.readable_resource_ids(
                (
                    resource_id
                    for path in paths
                    for resource_id in _path_resource_ids(path)
                ),
                scope=permission_scope,
            )
        )
        return [
            path
            for path in paths
            if _path_resource_ids(path).issubset(readable_resource_ids)
        ]

    async def _ensure_sources_readable(
        self,
        evidence: list[PublishedGraphEvidence],
        permission_scope: PermissionScope,
    ) -> None:
        """state 写入前再核一次证据资源权限，避免提交不可读节点。"""
        resource_ids = list(
            dict.fromkeys(record.evidence.resource_id for record in evidence)
        )
        readable_resource_ids = set(
            await self._authorizer.readable_resource_ids(
                resource_ids,
                scope=permission_scope,
            )
        )
        denied = next(
            (
                resource_id
                for resource_id in resource_ids
                if resource_id not in readable_resource_ids
            ),
            None,
        )
        if denied is not None:
            raise GraphAccessRevokedError(denied)


def _to_path_view(
    path: TraversedPath,
    evidence_by_edge: dict[str, list[PublishedGraphEvidence]],
) -> tuple[GraphPathView, list[PublishedGraphEvidence]]:
    """把领域路径投影为自然路径和按事实方向排列的关系证据。"""
    text, _ = _render_path(path)
    retained_records: list[PublishedGraphEvidence] = []
    relation_views: list[GraphRelationView] = []

    for edge in path.edges:
        records = evidence_by_edge[edge.edge_id]
        retained_records.extend(records)
        relation_views.append(
            GraphRelationView(
                source=GraphRelationEndpointView(
                    node_id=edge.source_node_id,
                    label=_node_label(path.nodes, edge.source_node_id),
                ),
                predicate=_edge_predicate(edge),
                target=GraphRelationEndpointView(
                    node_id=edge.target_node_id,
                    label=_node_label(path.nodes, edge.target_node_id),
                ),
                relation_evidence=[_to_evidence_view(record) for record in records],
            )
        )

    return (
        GraphPathView(
            path=text,
            relations=relation_views,
        ),
        retained_records,
    )


def _to_seed_node_view(node: KnowledgeNode) -> GraphNodeView:
    return GraphNodeView(
        node_id=node.node_id,
        label=node.label,
        kind=node.kind,
        entity_type=node.entity_type,
        role=GraphNodeRole.SEED,
    )


def _to_discovered_node_view(
    node: KnowledgeNode,
    evidence: list[PublishedGraphEvidence],
) -> DiscoveredKnowledgeNodeView:
    return DiscoveredKnowledgeNodeView(
        node_id=node.node_id,
        label=node.label,
        kind=node.kind,
        entity_type=node.entity_type,
        mention_evidence=[_to_evidence_view(record) for record in evidence],
    )


def _to_evidence_view(record: PublishedGraphEvidence) -> GraphEvidenceRefView:
    return GraphEvidenceRefView(
        resource_id=record.evidence.resource_id,
        reading_block_id=record.evidence.reading_block_id,
        quote=record.evidence.quote,
    )


def _deduplicate_evidence(
    records: list[PublishedGraphEvidence],
    *,
    limit: int | None = None,
) -> list[PublishedGraphEvidence]:
    """按 evidence_id 保留首次出现顺序，并应用调用方给出的条数上限。"""
    result: list[PublishedGraphEvidence] = []
    seen_ids: set[str] = set()
    for record in records:
        evidence_id = record.evidence.evidence_id
        if evidence_id in seen_ids:
            continue
        seen_ids.add(evidence_id)
        result.append(record)
        if limit is not None and len(result) >= limit:
            break
    return result


def _render_path(path: TraversedPath) -> tuple[str, list[str]]:
    """按遍历顺序生成可读文本，同时保留事实方向。"""
    if not path.nodes or len(path.edges) != len(path.nodes) - 1:
        raise RuntimeError("graph path must contain one edge between adjacent nodes")

    parts = [path.nodes[0].label]
    relations: list[str] = []
    for current, following, edge in zip(
        path.nodes[:-1],
        path.nodes[1:],
        path.edges,
        strict=True,
    ):
        relation = _edge_predicate(edge)
        if (
            edge.source_node_id == current.node_id
            and edge.target_node_id == following.node_id
        ):
            parts.extend((f" -[{relation}]-> ", following.label))
            source, target = current, following
        elif (
            edge.source_node_id == following.node_id
            and edge.target_node_id == current.node_id
        ):
            parts.extend((f" <-[{relation}]- ", following.label))
            source, target = following, current
        else:
            raise RuntimeError(
                f"graph edge {edge.edge_id} does not connect adjacent path nodes"
            )
        relations.append(
            f"{source.label} -[{relation}]-> "
            f"{target.label}"
        )
    return "".join(parts), relations


def _edge_predicate(edge: TraversedEdge) -> str:
    """把边投影为公开谓词协议（GraphRelationView.predicate: str）。

    RELATED_TO 用具体 predicate，其余类型用 relation_type 值；
    RELATED_TO 缺 predicate 属于权威图数据损坏，在边界直接抛错。
    """
    if edge.relation_type is not KnowledgeRelationType.RELATED_TO:
        return edge.relation_type.value
    if not edge.predicate:
        raise RuntimeError(f"RELATED_TO edge {edge.edge_id} is missing predicate")
    return edge.predicate


def _node_label(nodes: list[KnowledgeNode], node_id: str) -> str:
    for node in nodes:
        if node.node_id == node_id:
            return node.label
    raise RuntimeError(f"path does not contain relation endpoint {node_id}")


def _path_resource_ids(path: TraversedPath) -> set[str]:
    return {
        *(
            evidence.resource_id
            for edge in path.edges
            for evidence in edge.evidence
        ),
        *(node.resource_id for node in path.nodes if node.resource_id is not None),
    }


def _build_graph_reading_block_views(
    records: Sequence[PublishedGraphEvidence],
) -> list[GraphReadingBlockView]:
    """按首次证据顺序去重完整 ReadingBlock，并彻底隔离检索 matches。"""
    blocks: dict[tuple[str, str], GraphReadingBlockView] = {}

    for record in records:
        resource_id = record.evidence.resource_id
        block_key = (resource_id, record.reading_block.block_id)
        if block_key in blocks:
            continue
        presentation = present_reading_block(
            record.reading_block,
            record.reading_block_sections,
        )
        blocks[block_key] = GraphReadingBlockView(
            resource_id=resource_id,
            reading_block_id=record.reading_block.block_id,
            text=presentation.text,
            sections=presentation.sections,
            page_range=format_page_range(record.reading_block.page_labels),
            anchor_labels=record.reading_block.anchor_labels,
        )

    return list(blocks.values())

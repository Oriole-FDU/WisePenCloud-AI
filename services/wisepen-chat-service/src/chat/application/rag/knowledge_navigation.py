from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from chat.application.rag.acl import RagPermissionAuthorizer
from chat.application.rag.evidence import RagEvidenceMaterializer, RagEvidenceUnavailableError
from chat.application.rag.graph_extraction import (
    KnowledgeEntityType,
    KnowledgeNodeKind,
    KnowledgeRelationProfile,
    KnowledgeRelationType,
)
from chat.application.rag.repositories import (
    KnowledgeGraphNavigationRepository,
    KnowledgeNavigationStateRepository,
)
from chat.application.rag.retrieval import RagPermissionScope, RagRetrievalRequest
from chat.application.rag.retrieval.locator import RagKnowledgeLocator
from chat.application.rag.section_navigation import RagSectionNavigator, RagSectionView

_CANDIDATE_LIMIT = 80


class KnowledgeNavigationDirection(StrEnum):
    IN = "in"  # 只沿指向 seed 节点的关系反向遍历。
    OUT = "out"  # 只沿从 seed 节点出发的关系正向遍历。
    BOTH = "both"  # 忽略边方向，双向遍历。


@dataclass(frozen=True, slots=True)
class KnowledgeMentionSource:
    resource_id: str  # RAG 命中所属资源，用于定位 Neo4j Resource 节点。
    chunk_id: str  # RAG 命中的 RetrievalChunk ID，用于反查该 chunk 的 MENTIONS。


@dataclass(frozen=True, slots=True)
class KnowledgeNavigationNode:
    node_id: str  # Neo4j 中的稳定知识节点 ID，也是后续 expand 的 seed ID。
    kind: KnowledgeNodeKind  # Entity、Resource 或 ExternalSource。
    label: str  # 面向 Agent 展示的节点名称。
    entity_type: KnowledgeEntityType | None = None  # Entity 的细分类型；Resource/ExternalSource 为 None。
    type_tags: tuple[str, ...] = ()  # Neo4j 节点保留的类型标签，供导航结果展示。
    available_relations: tuple[KnowledgeRelationType, ...] = ()  # 当前节点可继续使用的关系类型。

    def to_payload(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "kind": self.kind.value,
            "label": self.label,
            "entity_type": self.entity_type.value if self.entity_type else None,
            "type_tags": list(self.type_tags),
            "available_relations": [item.value for item in self.available_relations],
        }


@dataclass(frozen=True, slots=True)
class KnowledgeNavigationEdge:
    edge_id: str  # Neo4j 中的稳定关系边 ID。
    source_node_id: str  # 关系语义上的起点，不随本次遍历方向变化。
    target_node_id: str  # 关系语义上的终点，不随本次遍历方向变化。
    relation_type: KnowledgeRelationType  # 关系类型，如 USES、CITES、DEPENDS_ON。
    relation_profile: KnowledgeRelationProfile  # 该关系所属 schema profile。
    predicate: str | None  # RELATED_TO 的具体谓词；其他关系为 None。
    evidence_resource_id: str  # 关系证据所在资源，决定后续回源分组。
    evidence_ref_ids: tuple[str, ...]  # 图抽取生成的 evidence ID，面向关系溯源。
    evidence_source_ref_ids: tuple[str, ...]  # 与 evidence_ref_ids 同序的 Mongo SourceRef ID。
    source_content_revision: str  # 写入该边时所依据的正文内容投影版本。
    relation_revision: str  # 写入该边时所依据的图关系投影版本。


@dataclass(frozen=True, slots=True)
class KnowledgeNavigationPath:
    nodes: tuple[KnowledgeNavigationNode, ...]  # 按遍历顺序排列的节点序列。
    edges: tuple[KnowledgeNavigationEdge, ...]  # 连接相邻 nodes 的边序列。

    @property
    def depth(self) -> int:
        return len(self.edges)

    def to_payload(self) -> dict[str, object]:
        return {
            "node_ids": [node.node_id for node in self.nodes],
            "edge_ids": [edge.edge_id for edge in self.edges],
            "depth": self.depth,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeNavigationState:
    state_id: str  # Redis 导航状态 ID，由 locate 创建并由后续 tool 传回。
    user_id: str  # 状态所属用户，用于拒绝跨用户复用 state_id。
    session_id: str  # 状态所属聊天会话，用于拒绝跨会话复用 state_id。
    root_query: str  # 创建该导航状态的初始问题，仅用于结果上下文展示。
    known_node_ids: tuple[str, ...] = ()  # 已返回的图节点和 Section ID，作为 read/expand 的输入白名单。


@dataclass(frozen=True, slots=True)
class KnowledgeGraphExpandRequest:
    seed_node_ids: tuple[str, ...]  # 本次遍历的起点，必须已出现在导航状态中。
    permission_scope: RagPermissionScope  # 当前用户身份，Neo4j 查询使用它构造 ACL 谓词。
    known_node_ids: tuple[str, ...] = ()  # 当前状态已经展示的节点，用于过滤重复路径。
    relation_types: tuple[KnowledgeRelationType, ...] = ()  # 限定可经过的关系类型；空表示不限制。
    direction: KnowledgeNavigationDirection = KnowledgeNavigationDirection.BOTH  # 相对 seed 的遍历方向。
    max_depth: int = 1  # 最大关系跳数；tool 当前限制为 1 或 2。
    limit: int = 10  # 最多返回的图路径数量。


@dataclass(frozen=True, slots=True)
class KnowledgeNavigationLocateResult:
    state: KnowledgeNavigationState  # locate 创建的导航状态。
    nodes: tuple[KnowledgeNavigationNode, ...]  # RAG 命中 chunk 通过 MENTIONS 反查出的图节点。
    sources: tuple[RagSectionView, ...]  # 命中 Section 的正文证据和标题树 frontier。


@dataclass(frozen=True, slots=True)
class KnowledgeNavigationExpandResult:
    state: KnowledgeNavigationState  # 本次 expand 校验通过的导航状态快照。
    nodes: tuple[KnowledgeNavigationNode, ...]  # 本次保留路径中的去重节点。
    edges: tuple[KnowledgeNavigationEdge, ...]  # 本次保留路径中的去重关系边。
    paths: tuple[KnowledgeNavigationPath, ...]  # 至少发现一个新节点的有界遍历路径。
    sources: tuple[RagSectionView, ...]  # 关系 evidence 回源后对应的 Section 来源。
    new_node_ids: tuple[str, ...]  # 已写入 Redis 状态、供下次 expand 使用的新图节点 ID。


@dataclass(frozen=True, slots=True)
class KnowledgeSectionReadResult:
    state: KnowledgeNavigationState  # 本次 read_sections 校验通过的导航状态快照。
    sections: tuple[RagSectionView, ...]  # 带全部 ReadingBlock 正文和 frontier 的 Section 视图。
    new_section_ids: tuple[str, ...]  # 已写入 Redis 状态、供下次读取使用的新 Section ID。


class KnowledgeNavigationStateNotFoundError(RuntimeError):
    pass


class KnowledgeNavigationStateInvalidatedError(RuntimeError):
    pass


class KnowledgeNavigationService:
    """提供基于 RAG 证据和知识图谱的增量导航能力。"""

    __slots__ = (
        "_evidence_materializer",
        "_graph_repository",
        "_locator",
        "_permission_authorizer",
        "_section_navigator",
        "_state_repository",
    )

    def __init__(
        self,
        *,
        locator: RagKnowledgeLocator,
        permission_authorizer: RagPermissionAuthorizer,
        graph_repository: KnowledgeGraphNavigationRepository,
        evidence_materializer: RagEvidenceMaterializer,
        section_navigator: RagSectionNavigator,
        state_repository: KnowledgeNavigationStateRepository,
    ) -> None:
        self._locator = locator
        self._permission_authorizer = permission_authorizer
        self._graph_repository = graph_repository
        self._evidence_materializer = evidence_materializer
        self._section_navigator = section_navigator
        self._state_repository = state_repository

    async def locate(
        self,
        *,
        query: str,
        max_results: int,
        session_id: str,
        permission_scope: RagPermissionScope,
    ) -> KnowledgeNavigationLocateResult:
        """根据查询建立知识导航初始状态。"""
        # 使用 RAG 找到与查询相关的上下文证据。
        hits = await self._locator.locate(
            RagRetrievalRequest(
                query=query,
                permission_scope=permission_scope,
                top_k=max_results,
                candidate_limit=_CANDIDATE_LIMIT,
            )
        )

        # 根据 evidence 来源定位已有知识图谱节点。
        nodes = await self._graph_repository.resolve_mentions(
            sources=tuple(
                KnowledgeMentionSource(
                    resource_id=hit.materialized_hit.resource_id,
                    chunk_id=hit.materialized_hit.chunk_id,
                )
                for hit in hits
            ),
            permission_scope=permission_scope,
        )

        # 创建会话级导航状态，记录当前已发现节点；
        # known_node_ids 同时包含 RAG 命中的 Section 节点和它们的 parent/previous/next/children frontier，
        # 后续 expand/read_sections 都基于这个集合做归属校验。
        state = await self._state_repository.create(
            user_id=permission_scope.user_id,
            session_id=session_id,
            root_query=query,
            known_node_ids=tuple(
                dict.fromkeys(
                    [node.node_id for node in nodes]
                    + [
                        section.section_id
                        for hit in hits
                        for section in (
                            hit.view.section,
                            hit.view.parent,
                            hit.view.previous,
                            hit.view.next,
                            *hit.view.children,
                        )
                        if section is not None
                    ]
                )
            ),
        )

        return KnowledgeNavigationLocateResult(
            state=state,
            nodes=nodes,
            sources=tuple(hit.view for hit in hits),
        )

    async def read_sections(
        self,
        *,
        state_id: str,
        resource_id: str,
        section_ids: tuple[str, ...],
        session_id: str,
        permission_scope: RagPermissionScope,
    ) -> KnowledgeSectionReadResult:
        """读取已发现的 Section 正文并返回下一层标题树 frontier。"""
        state = await self._state_repository.get(state_id)
        if (
            state is None
            or state.user_id != permission_scope.user_id
            or state.session_id != session_id
        ):
            raise KnowledgeNavigationStateNotFoundError(state_id)
        # 防止客户端提交未在导航上下文中出现的 Section ID。
        if not set(section_ids).issubset(state.known_node_ids):
            raise KnowledgeNavigationStateInvalidatedError(state_id)

        # Section 读取涉及正文回源，必须做最终 ACL 校验（与 evidence materializer 保持一致）。
        accessible = await self._permission_authorizer.accessible_resource_ids(
            resource_ids=(resource_id,),
            scope=permission_scope,
        )
        if resource_id not in accessible:
            raise RagEvidenceUnavailableError(
                f"resource permission changed before section read: {resource_id}"
            )

        sections = await self._section_navigator.read_sections(
            resource_id=resource_id,
            section_ids=section_ids,
        )
        # 展开当前 Section 的 parent/previous/next/children frontier，用于下一轮 read/expand。
        discovered_ids = tuple(
            dict.fromkeys(
                section.section_id
                for view in sections
                for section in (
                    view.parent,
                    view.previous,
                    view.next,
                    *view.children,
                )
                if section is not None
            )
        )
        new_section_ids = tuple(
            section_id
            for section_id in discovered_ids
            if section_id not in state.known_node_ids
        )
        # 把新发现的 Section 原子地加入 known_node_ids，供下次 read 校验。
        if not await self._state_repository.add_known_nodes(
            state_id=state.state_id,
            node_ids=new_section_ids,
        ):
            raise KnowledgeNavigationStateNotFoundError(state_id)

        return KnowledgeSectionReadResult(
            state=state,
            sections=sections,
            new_section_ids=new_section_ids,
        )

    async def expand(
        self,
        *,
        state_id: str,
        node_ids: tuple[str, ...],
        relation_types: tuple[KnowledgeRelationType, ...],
        direction: KnowledgeNavigationDirection,
        max_depth: int,
        max_results: int,
        session_id: str,
        permission_scope: RagPermissionScope,
    ) -> KnowledgeNavigationExpandResult:
        """从已有节点继续展开知识关系。"""
        # 校验导航状态归属，避免跨用户或跨会话访问。
        state = await self._state_repository.get(state_id)

        if (
            state is None
            or state.user_id != permission_scope.user_id
            or state.session_id != session_id
        ):
            raise KnowledgeNavigationStateNotFoundError(state_id)

        # 防止客户端提交未在当前导航上下文中出现的节点。
        if not set(node_ids).issubset(state.known_node_ids):
            raise KnowledgeNavigationStateInvalidatedError(state_id)

        paths = await self._graph_repository.expand(
            KnowledgeGraphExpandRequest(
                seed_node_ids=node_ids,
                permission_scope=permission_scope,
                known_node_ids=state.known_node_ids,
                relation_types=relation_types,
                direction=direction,
                max_depth=max_depth,
                limit=max_results,
            )
        )

        # 只保留能够发现新节点的路径，避免重复返回当前导航状态已经展示过的内容。
        paths = tuple(
            path
            for path in paths
            if any(node.node_id not in state.known_node_ids for node in path.nodes[1:])
        )

        # 节点和边按 ID 去重并稳定排序，保证多次展开的结果一致。
        nodes_by_id = {node.node_id: node for path in paths for node in path.nodes}
        nodes = tuple(nodes_by_id[node_id] for node_id in sorted(nodes_by_id))

        edges_by_id = {edge.edge_id: edge for path in paths for edge in path.edges}
        edges = tuple(edges_by_id[edge_id] for edge_id in sorted(edges_by_id))

        # 收集边上的 evidence 引用，按 resource 聚合后批量回源。
        refs_by_resource: dict[str, list[str]] = {}
        for edge in edges:
            refs_by_resource.setdefault(edge.evidence_resource_id, []).extend(
                edge.evidence_source_ref_ids
            )

        materialized = await self._evidence_materializer.materialize_refs(
            refs_by_resource, permission_scope=permission_scope
        )
        sources = await self._section_navigator.build_sources(materialized)

        new_node_ids = tuple(
            node.node_id for node in nodes if node.node_id not in state.known_node_ids
        )

        # 原子更新导航状态：新发现节点加入 known_node_ids，供下一次 expand 校验。
        if not await self._state_repository.add_known_nodes(
            state_id=state.state_id, node_ids=new_node_ids
        ):
            raise KnowledgeNavigationStateNotFoundError(state_id)

        return KnowledgeNavigationExpandResult(
            state=state,
            nodes=nodes,
            edges=edges,
            paths=paths,
            sources=sources,
            new_node_ids=new_node_ids,
        )

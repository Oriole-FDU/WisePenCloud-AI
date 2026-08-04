from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum

from rag.application.rag.acl import RagPermissionAuthorizer
from rag.application.rag.evidence import RagEvidenceMaterializer, RagEvidenceUnavailableError
from rag.application.rag.graph_extraction import (
    KnowledgeEntityType,
    KnowledgeNodeKind,
    KnowledgeRelationType,
)
from rag.application.rag.repositories import (
    KnowledgeGraphNavigationRepository,
    KnowledgeNavigationStateRepository,
)
from rag.application.rag.retrieval import (
    RagCandidateRetriever,
    RagPermissionScope,
    RagRetrievalRequest,
)
from rag.application.rag.section_navigation import RagSectionNavigator, RagSectionView
from rag.utils.ranking import (
    RankCandidate,
    RankingPipeline,
    RankQuery,
    RankRequest,
)

_CANDIDATE_LIMIT = 80
_EXPAND_CANDIDATE_MULTIPLIER = 4


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

    def to_payload(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "kind": self.kind.value,
            "label": self.label,
            "entity_type": self.entity_type.value if self.entity_type else None,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeNavigationEdge:
    edge_id: str  # Neo4j 中的稳定关系边 ID。
    source_node_id: str  # 关系语义上的起点，不随本次遍历方向变化。
    target_node_id: str  # 关系语义上的终点，不随本次遍历方向变化。
    relation_type: KnowledgeRelationType  # 关系类型，如 USES、CITES、DEPENDS_ON。
    predicate: str | None  # RELATED_TO 的具体谓词；其他关系为 None。
    evidence_resource_id: str  # 关系证据所在资源，决定后续回源分组。
    evidence_quotes: tuple[str, ...]  # 已按原文 offset 校验的关系证据。
    evidence_source_ref_ids: tuple[str, ...]  # 用于内部正文回源的 Mongo SourceRef ID。


@dataclass(frozen=True, slots=True)
class KnowledgeNavigationPath:
    nodes: tuple[KnowledgeNavigationNode, ...]  # 按遍历顺序排列的节点序列。
    edges: tuple[KnowledgeNavigationEdge, ...]  # 连接相邻 nodes 的边序列。

    def to_payload(self) -> dict[str, object]:
        return {
            "node_ids": [node.node_id for node in self.nodes],
            "edge_ids": [edge.edge_id for edge in self.edges],
        }


@dataclass(frozen=True, slots=True)
class KnowledgeNavigationState:
    state_id: str  # Redis 导航状态 ID，由 locate 创建并由后续 tool 传回。
    user_id: str  # 状态所属用户，用于拒绝跨用户复用 state_id。
    session_id: str  # 状态所属聊天会话，用于拒绝跨会话复用 state_id。
    root_query: str  # 创建该导航状态的初始问题，用作 expand 排序回退。
    known_graph_node_ids: tuple[str, ...] = ()  # expand 可用的图节点白名单。
    known_sections: tuple[tuple[str, str], ...] = ()  # section_id 与所属 resource_id。


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
    state_id: str  # locate 创建的导航状态 ID。
    nodes: tuple[KnowledgeNavigationNode, ...]  # RAG 命中 chunk 通过 MENTIONS 反查出的图节点。
    sources: tuple[RagSectionView, ...]  # 命中 Section 的正文证据和标题树 frontier。


@dataclass(frozen=True, slots=True)
class KnowledgeNavigationExpandResult:
    state_id: str  # 本次 expand 使用的导航状态 ID。
    nodes: tuple[KnowledgeNavigationNode, ...]  # 本次保留路径中的去重节点。
    edges: tuple[KnowledgeNavigationEdge, ...]  # 本次保留路径中的去重关系边。
    paths: tuple[KnowledgeNavigationPath, ...]  # 至少发现一个新节点的有界遍历路径。
    sources: tuple[RagSectionView, ...]  # 关系 evidence 回源后对应的 Section 来源。


@dataclass(frozen=True, slots=True)
class KnowledgeSectionReadResult:
    state_id: str  # 本次 read_sections 使用的导航状态 ID。
    sections: tuple[RagSectionView, ...]  # 带全部 ReadingBlock 正文和 frontier 的 Section 视图。


class KnowledgeNavigationStateNotFoundError(RuntimeError):
    pass


class KnowledgeNavigationStateInvalidatedError(RuntimeError):
    pass


class KnowledgeNavigationService:
    """提供基于 RAG 证据和知识图谱的增量导航能力。"""

    __slots__ = (
        "_evidence_materializer",
        "_graph_repository",
        "_path_ranking_pipeline",
        "_permission_authorizer",
        "_retriever",
        "_section_navigator",
        "_state_repository",
    )

    def __init__(
        self,
        *,
        retriever: RagCandidateRetriever,
        permission_authorizer: RagPermissionAuthorizer,
        graph_repository: KnowledgeGraphNavigationRepository,
        evidence_materializer: RagEvidenceMaterializer,
        section_navigator: RagSectionNavigator,
        state_repository: KnowledgeNavigationStateRepository,
        path_ranking_pipeline: RankingPipeline,
    ) -> None:
        self._retriever = retriever
        self._permission_authorizer = permission_authorizer
        self._graph_repository = graph_repository
        self._evidence_materializer = evidence_materializer
        self._section_navigator = section_navigator
        self._state_repository = state_repository
        self._path_ranking_pipeline = path_ranking_pipeline

    async def locate(
        self,
        *,
        query: str,
        max_results: int,
        session_id: str,
        permission_scope: RagPermissionScope,
    ) -> KnowledgeNavigationLocateResult:
        """根据查询建立知识导航初始状态。"""
        request = RagRetrievalRequest(
            query=query,
            permission_scope=permission_scope,
            top_k=max_results,
            candidate_limit=_CANDIDATE_LIMIT,
        )
        candidates = await self._retriever.retrieve(request)
        materialized_hits = await self._evidence_materializer.materialize(
            candidates=candidates,
            permission_scope=permission_scope,
        )
        views = await self._section_navigator.build_hits(materialized_hits)

        # 根据 evidence 来源定位已有知识图谱节点。
        nodes = await self._graph_repository.resolve_mentions(
            sources=tuple(
                KnowledgeMentionSource(
                    resource_id=source.source_ref.resource_id,
                    chunk_id=source.source_ref.chunk_id,
                )
                for view in views
                for source in view.sources
            ),
            permission_scope=permission_scope,
        )

        known_sections = {
            section.section_id: section.resource_id
            for view in views
            for section in (
                view.section,
                view.parent,
                view.previous,
                view.next,
                *view.children,
            )
            if section is not None
        }
        state = await self._state_repository.create(
            user_id=permission_scope.user_id,
            session_id=session_id,
            root_query=query,
            known_graph_node_ids=tuple(node.node_id for node in nodes),
            known_sections=known_sections,
        )

        return KnowledgeNavigationLocateResult(
            state_id=state.state_id,
            nodes=nodes,
            sources=views,
        )

    async def read_sections(
        self,
        *,
        state_id: str,
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
        known_sections = dict(state.known_sections)
        if not set(section_ids).issubset(known_sections):
            raise KnowledgeNavigationStateInvalidatedError(state_id)

        resource_ids = tuple(
            dict.fromkeys(known_sections[section_id] for section_id in section_ids)
        )
        accessible = await self._permission_authorizer.accessible_resource_ids(
            resource_ids=resource_ids,
            scope=permission_scope,
        )
        inaccessible = tuple(
            resource_id for resource_id in resource_ids if resource_id not in accessible
        )
        if inaccessible:
            raise RagEvidenceUnavailableError(
                "resource permission changed before section read: " + ", ".join(inaccessible)
            )

        section_groups = await asyncio.gather(
            *(
                self._section_navigator.read_sections(
                    resource_id=resource_id,
                    section_ids=tuple(
                        section_id
                        for section_id in section_ids
                        if known_sections[section_id] == resource_id
                    ),
                )
                for resource_id in resource_ids
            )
        )
        sections_by_id = {
            view.section.section_id: view
            for group in section_groups
            for view in group
        }
        sections = tuple(sections_by_id[section_id] for section_id in section_ids)
        discovered_sections = {
            section.section_id: section.resource_id
            for view in sections
            for section in (view.parent, view.previous, view.next, *view.children)
            if section is not None
        }
        new_sections = {
            section_id: resource_id
            for section_id, resource_id in discovered_sections.items()
            if section_id not in known_sections
        }
        if not await self._state_repository.add_known_sections(
            state_id=state.state_id,
            sections=new_sections,
        ):
            raise KnowledgeNavigationStateNotFoundError(state_id)

        return KnowledgeSectionReadResult(
            state_id=state.state_id,
            sections=sections,
        )

    async def expand(
        self,
        *,
        state_id: str,
        node_ids: tuple[str, ...],
        query: str | None,
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
        if not set(node_ids).issubset(state.known_graph_node_ids):
            raise KnowledgeNavigationStateInvalidatedError(state_id)

        # Neo4j 只负责按图约束生成合法候选；自然语言意图在应用层排序，
        # 避免 query 改写遍历语义，也避免固定图顺序过早截断相关路径。
        candidate_limit = min(
            max_results * _EXPAND_CANDIDATE_MULTIPLIER,
            _CANDIDATE_LIMIT,
        )
        paths = await self._graph_repository.expand(
            KnowledgeGraphExpandRequest(
                seed_node_ids=node_ids,
                permission_scope=permission_scope,
                known_node_ids=state.known_graph_node_ids,
                relation_types=relation_types,
                direction=direction,
                max_depth=max_depth,
                limit=candidate_limit,
            )
        )

        # 只保留能够发现新节点的路径，避免重复返回当前导航状态已经展示过的内容。
        paths = tuple(
            path
            for path in paths
            if any(node.node_id not in state.known_graph_node_ids for node in path.nodes[1:])
        )

        effective_query = query or state.root_query
        ranked_paths = await self._path_ranking_pipeline.arank(
            RankRequest(
                query=RankQuery(text=effective_query),
                candidates=tuple(
                    RankCandidate(
                        candidate_id=str(i),
                        text=_path_ranking_text(p),
                        fields={
                            "nodes": "\n".join(n.label for n in p.nodes),
                            "relations": "\n".join(
                                " ".join(filter(None, (e.relation_type.value, e.predicate)))
                                for e in p.edges
                            ),
                        },
                        prior_rank=i + 1,
                    )
                    for i, p in enumerate(paths)
                ),
                top_k=max_results,
                candidate_limit=candidate_limit,
            )
        )

        paths = tuple(paths[int(item.candidate_id)] for item in ranked_paths.ranked)

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
            node.node_id for node in nodes if node.node_id not in state.known_graph_node_ids
        )

        # 原子更新导航状态：新发现节点加入 known_node_ids，供下一次 expand 校验。
        if not await self._state_repository.add_known_graph_nodes(
            state_id=state.state_id, node_ids=new_node_ids
        ):
            raise KnowledgeNavigationStateNotFoundError(state_id)

        return KnowledgeNavigationExpandResult(
            state_id=state.state_id,
            nodes=nodes,
            edges=edges,
            paths=paths,
            sources=sources,
        )


def _path_ranking_text(path: KnowledgeNavigationPath) -> str:
    nodes = " -> ".join(node.label for node in path.nodes)
    relations = "\n".join(
        " | ".join(
            value
            for value in (
                edge.relation_type.value,
                edge.predicate,
            )
            if value
        )
        for edge in path.edges
    )
    return f"Path: {nodes}\nRelations:\n{relations}"

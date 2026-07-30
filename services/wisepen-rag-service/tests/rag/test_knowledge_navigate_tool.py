from __future__ import annotations

from typing import Any

import pytest

from rag.application.rag.evidence import RagMaterializedSource
from rag.application.rag.graph_extraction import (
    KnowledgeEntityType,
    KnowledgeNodeKind,
    KnowledgeRelationType,
)
from rag.application.rag.knowledge_navigation import (
    KnowledgeNavigationDirection,
    KnowledgeNavigationEdge,
    KnowledgeNavigationNode,
    KnowledgeNavigationPath,
    KnowledgeNavigationService,
    KnowledgeNavigationState,
    KnowledgeNavigationStateInvalidatedError,
)
from rag.application.rag.ingestion import (
    RagSectionNode,
    RagSectionReadingBlock,
    RagSourceRef,
)
from rag.application.rag.section_navigation import RagSectionView
from rag.application.rag.retrieval import RagPermissionScope
from common.utils.chunkers import SourceSpan
from common.utils.ranking import RankedCandidate, RankResult


class _Retriever:
    def __init__(self, candidates=()) -> None:
        self.candidates = candidates
        self.requests = []

    async def retrieve(self, request):
        self.requests.append(request)
        return self.candidates


class _StateRepository:
    def __init__(self) -> None:
        self.state: KnowledgeNavigationState | None = None
        self.created: list[dict[str, Any]] = []
        self.added: list[tuple[str, tuple[str, ...]]] = []
        self.added_sections: list[tuple[str, dict[str, str]]] = []

    async def create(self, **kwargs):
        self.created.append(kwargs)
        self.state = KnowledgeNavigationState(
            state_id="kns_test",
            user_id=kwargs["user_id"],
            session_id=kwargs["session_id"],
            root_query=kwargs["root_query"],
            known_graph_node_ids=kwargs["known_graph_node_ids"],
            known_sections=tuple(sorted(kwargs["known_sections"].items())),
        )
        return self.state

    async def get(self, state_id):
        if self.state is not None and self.state.state_id == state_id:
            return self.state
        return None

    async def add_known_graph_nodes(self, *, state_id, node_ids):
        if self.state is None or self.state.state_id != state_id:
            return False
        self.added.append((state_id, node_ids))
        self.state = KnowledgeNavigationState(
            state_id=self.state.state_id,
            user_id=self.state.user_id,
            session_id=self.state.session_id,
            root_query=self.state.root_query,
            known_graph_node_ids=tuple(
                dict.fromkeys((*self.state.known_graph_node_ids, *node_ids))
            ),
            known_sections=self.state.known_sections,
        )
        return True

    async def add_known_sections(self, *, state_id, sections):
        if self.state is None or self.state.state_id != state_id:
            return False
        self.added_sections.append((state_id, dict(sections)))
        known_sections = dict(self.state.known_sections)
        known_sections.update(sections)
        self.state = KnowledgeNavigationState(
            state_id=self.state.state_id,
            user_id=self.state.user_id,
            session_id=self.state.session_id,
            root_query=self.state.root_query,
            known_graph_node_ids=self.state.known_graph_node_ids,
            known_sections=tuple(sorted(known_sections.items())),
        )
        return True


class _GraphRepository:
    def __init__(self, *, mentions=(), paths=()) -> None:
        self.mentions = mentions
        self.paths = paths
        self.mention_requests = []
        self.expand_requests = []

    async def resolve_mentions(self, **kwargs):
        self.mention_requests.append(kwargs)
        return self.mentions

    async def expand(self, request):
        self.expand_requests.append(request)
        return self.paths


class _EvidenceMaterializer:
    def __init__(self, sources=(), materialized_hits=()) -> None:
        self.sources = sources
        self.materialized_hits = materialized_hits
        self.requests = []
        self.materialize_requests = []

    async def materialize(self, *, candidates, permission_scope):
        self.materialize_requests.append((candidates, permission_scope))
        return self.materialized_hits

    async def materialize_refs(self, request, permission_scope):
        self.requests.append(request)
        return self.sources


class _SectionNavigator:
    def __init__(self, *, hit_views=(), read_views=(), source_views=()) -> None:
        self.hit_views = hit_views
        self.read_views = read_views
        self.source_views = source_views
        self.hit_requests = []
        self.read_requests = []

    async def build_hits(self, hits):
        self.hit_requests.append(hits)
        return self.hit_views

    async def read_sections(self, **kwargs):
        self.read_requests.append(kwargs)
        return self.read_views

    async def build_sources(self, sources):
        return self.source_views


class _PermissionAuthorizer:
    async def accessible_resource_ids(self, resource_ids, scope):
        return frozenset(resource_ids)


class _PathRankingPipeline:
    def __init__(self) -> None:
        self.requests = []

    async def arank(self, request):
        self.requests.append(request)
        candidates = sorted(
            request.candidates,
            key=lambda candidate: request.query.text not in candidate.text,
        )[: request.top_k]
        return RankResult(
            ranked=tuple(
                RankedCandidate(
                    candidate=candidate,
                    rank=index,
                    score=1.0,
                )
                for index, candidate in enumerate(candidates, start=1)
            ),
            total_candidates=len(request.candidates),
        )


@pytest.mark.asyncio
async def test_locate_registers_tree_frontier() -> None:
    located = _located_section()
    retriever = _Retriever(("candidate",))
    materializer = _EvidenceMaterializer(materialized_hits=("materialized",))
    section_navigator = _SectionNavigator(hit_views=(located,))
    state_repository = _StateRepository()
    graph_repository = _GraphRepository(
        mentions=(_entity_node("kn_alpha", "Alpha"),)
    )
    service = _service(
        retriever=retriever,
        graph_repository=graph_repository,
        evidence_materializer=materializer,
        section_navigator=section_navigator,
        state_repository=state_repository,
    )

    result = await service.locate(
        query="核心概念是什么？",
        max_results=2,
        session_id="session-1",
        permission_scope=_permission_scope(),
    )

    section = result.sources[0]
    assert section.section.section_id == "section-current"
    assert section.parent.section_id == "section-root"
    assert section.previous.section_id == "section-previous"
    assert section.children[0].section_id == "section-child"
    assert state_repository.created[0]["known_graph_node_ids"] == ("kn_alpha",)
    assert state_repository.created[0]["known_sections"] == {
        "section-current": "resource-1",
        "section-root": "resource-1",
        "section-previous": "resource-1",
        "section-child": "resource-1",
    }
    request = retriever.requests[0]
    assert request.query == "核心概念是什么？"
    assert request.top_k == 2
    assert materializer.materialize_requests == [
        (("candidate",), request.permission_scope)
    ]
    assert section_navigator.hit_requests == [("materialized",)]


@pytest.mark.asyncio
async def test_sections_read_selected_node_and_add_new_frontier() -> None:
    located = _located_section()
    view = located
    next_section = _section(
        "section-next",
        "后续",
        parent_section_id="section-root",
        ordinal=2,
    )
    view = RagSectionView(
        section=view.section,
        reading_blocks=(
            view.reading_blocks[0],
            _reading_block("block-2", 1, "本节后续正文。"),
        ),
        parent=view.parent,
        previous=view.previous,
        next=next_section,
        children=view.children,
    )
    state_repository = _StateRepository()
    state_repository.state = KnowledgeNavigationState(
        state_id="kns_test",
        user_id="user-1",
        session_id="session-1",
        root_query="核心概念",
        known_sections=(("section-current", "resource-1"),),
    )
    navigator = _SectionNavigator(read_views=(view,))
    service = _service(
        section_navigator=navigator,
        state_repository=state_repository,
    )

    result = await service.read_sections(
        state_id="kns_test",
        section_ids=("section-current",),
        session_id="session-1",
        permission_scope=_permission_scope(),
    )

    section = result.sections[0]
    assert [block.block_id for block in section.reading_blocks] == ["block-1", "block-2"]
    assert navigator.read_requests == [
        {
            "resource_id": "resource-1",
            "section_ids": ("section-current",),
        }
    ]
    assert state_repository.added_sections == [
        (
            "kns_test",
            {
                "section-root": "resource-1",
                "section-previous": "resource-1",
                "section-next": "resource-1",
                "section-child": "resource-1",
            },
        )
    ]


@pytest.mark.asyncio
async def test_graph_expand_remains_cross_document_relation_navigation() -> None:
    located = _located_section()
    state_repository = _StateRepository()
    state_repository.state = KnowledgeNavigationState(
        state_id="kns_test",
        user_id="user-1",
        session_id="session-1",
        root_query="Alpha",
        known_graph_node_ids=("kn_alpha",),
    )
    edge = KnowledgeNavigationEdge(
        edge_id="kne_1",
        source_node_id="kn_alpha",
        target_node_id="kn_beta",
        relation_type=KnowledgeRelationType.DEPENDS_ON,
        predicate=None,
        evidence_resource_id="resource-1",
        evidence_quotes=("Alpha depends on Beta.",),
        evidence_source_ref_ids=("source-1",),
    )
    path = KnowledgeNavigationPath(
        nodes=(
            _entity_node("kn_alpha", "Alpha"),
            _entity_node("kn_beta", "Beta"),
        ),
        edges=(edge,),
    )
    graph_repository = _GraphRepository(paths=(path,))
    path_ranking_pipeline = _PathRankingPipeline()
    service = _service(
        graph_repository=graph_repository,
        evidence_materializer=_EvidenceMaterializer(located.sources),
        section_navigator=_SectionNavigator(source_views=(located,)),
        state_repository=state_repository,
        path_ranking_pipeline=path_ranking_pipeline,
    )

    result = await service.expand(
        state_id="kns_test",
        node_ids=("kn_alpha",),
        query=None,
        relation_types=(KnowledgeRelationType.DEPENDS_ON,),
        direction=KnowledgeNavigationDirection.BOTH,
        max_depth=1,
        max_results=10,
        session_id="session-1",
        permission_scope=_permission_scope(),
    )

    assert result.edges == (edge,)
    assert result.sources[0].section.section_id == "section-current"
    assert state_repository.added == [("kns_test", ("kn_beta",))]
    assert path_ranking_pipeline.requests[0].query.text == "Alpha"
    assert graph_repository.expand_requests[0].limit == 40


@pytest.mark.asyncio
async def test_graph_expand_query_ranks_paths_before_materialization() -> None:
    state_repository = _StateRepository()
    state_repository.state = KnowledgeNavigationState(
        state_id="kns_test",
        user_id="user-1",
        session_id="session-1",
        root_query="初始问题",
        known_graph_node_ids=("kn_alpha",),
    )
    beta_path = _path(
        target_id="kn_beta",
        target_label="Beta",
        edge_id="kne_beta",
        source_ref_id="source-beta",
    )
    gamma_path = _path(
        target_id="kn_gamma",
        target_label="Gamma",
        edge_id="kne_gamma",
        source_ref_id="source-gamma",
    )
    graph_repository = _GraphRepository(paths=(beta_path, gamma_path))
    path_ranking_pipeline = _PathRankingPipeline()
    materializer = _EvidenceMaterializer()
    service = _service(
        graph_repository=graph_repository,
        evidence_materializer=materializer,
        state_repository=state_repository,
        path_ranking_pipeline=path_ranking_pipeline,
    )

    result = await service.expand(
        state_id="kns_test",
        node_ids=("kn_alpha",),
        query="Gamma",
        relation_types=(),
        direction=KnowledgeNavigationDirection.BOTH,
        max_depth=1,
        max_results=1,
        session_id="session-1",
        permission_scope=_permission_scope(),
    )

    assert path_ranking_pipeline.requests[0].query.text == "Gamma"
    assert graph_repository.expand_requests[0].limit == 4
    assert result.paths[0].to_payload() == {
        "node_ids": ["kn_alpha", "kn_gamma"],
        "edge_ids": ["kne_gamma"],
    }
    assert materializer.requests == [{"resource-1": ["source-gamma"]}]
    assert state_repository.added == [("kns_test", ("kn_gamma",))]


@pytest.mark.asyncio
async def test_sections_tool_rejects_unknown_section() -> None:
    state_repository = _StateRepository()
    state_repository.state = KnowledgeNavigationState(
        state_id="kns_test",
        user_id="user-1",
        session_id="session-1",
        root_query="Alpha",
        known_sections=(("section-current", "resource-1"),),
    )
    service = _service(state_repository=state_repository)

    with pytest.raises(KnowledgeNavigationStateInvalidatedError):
        await service.read_sections(
            state_id="kns_test",
            section_ids=("section-unknown",),
            session_id="session-1",
            permission_scope=_permission_scope(),
        )


def _permission_scope() -> RagPermissionScope:
    return RagPermissionScope(user_id="user-1", group_role_map={})


def _service(
    *,
    retriever=None,
    graph_repository=None,
    evidence_materializer=None,
    section_navigator=None,
    state_repository=None,
    path_ranking_pipeline=None,
) -> KnowledgeNavigationService:
    return KnowledgeNavigationService(
        retriever=retriever or _Retriever(),
        permission_authorizer=_PermissionAuthorizer(),
        graph_repository=graph_repository or _GraphRepository(),
        evidence_materializer=evidence_materializer or _EvidenceMaterializer(),
        section_navigator=section_navigator or _SectionNavigator(),
        state_repository=state_repository or _StateRepository(),
        path_ranking_pipeline=path_ranking_pipeline or _PathRankingPipeline(),
    )


def _entity_node(node_id: str, label: str) -> KnowledgeNavigationNode:
    return KnowledgeNavigationNode(
        node_id=node_id,
        kind=KnowledgeNodeKind.ENTITY,
        label=label,
        entity_type=KnowledgeEntityType.CONCEPT,
    )


def _path(
    *,
    target_id: str,
    target_label: str,
    edge_id: str,
    source_ref_id: str,
) -> KnowledgeNavigationPath:
    return KnowledgeNavigationPath(
        nodes=(
            _entity_node("kn_alpha", "Alpha"),
            _entity_node(target_id, target_label),
        ),
        edges=(
            KnowledgeNavigationEdge(
                edge_id=edge_id,
                source_node_id="kn_alpha",
                target_node_id=target_id,
                relation_type=KnowledgeRelationType.RELATED_TO,
                predicate=f"related to {target_label}",
                evidence_resource_id="resource-1",
                evidence_quotes=(f"Alpha is related to {target_label}.",),
                evidence_source_ref_ids=(source_ref_id,),
            ),
        ),
    )


def _located_section() -> RagSectionView:
    source = RagMaterializedSource(
        source_ref=RagSourceRef(
            ref_id="source-1",
            resource_id="resource-1",
            document_version=3,
            chunk_id="chunk-1",
            section_id="section-current",
            section_path=("课程", "核心概念"),
            source_spans=(SourceSpan(20, 34),),
            page_label="2",
            anchor_labels=("Definition 1",),
        ),
        content="核心概念的直接证据。",
    )
    reading_block = _reading_block(
        "block-1",
        0,
        "本节命中附近的完整阅读块。",
    )
    current = _section(
        "section-current",
        "核心概念",
        parent_section_id="section-root",
        ordinal=1,
        summary="核心概念摘要",
    )
    return RagSectionView(
        section=current,
        sources=(source,),
        reading_blocks=(reading_block,),
        parent=_section("section-root", "课程"),
        previous=_section(
            "section-previous",
            "背景",
            parent_section_id="section-root",
        ),
        children=(
            _section(
                "section-child",
                "例子",
                parent_section_id="section-current",
            ),
        ),
    )


def _section(
    section_id: str,
    title: str,
    *,
    parent_section_id: str | None = None,
    ordinal: int = 0,
    summary: str | None = None,
) -> RagSectionNode:
    return RagSectionNode(
        section_id=section_id,
        resource_id="resource-1",
        document_version=3,
        title=title,
        level=1 if parent_section_id is None else 2,
        parent_section_id=parent_section_id,
        ordinal=ordinal,
        section_path=(title,),
        summary=summary or f"{title}摘要",
        own_start=ordinal * 10,
        own_end=ordinal * 10 + 10,
        subtree_end=ordinal * 10 + 10,
    )


def _reading_block(
    block_id: str,
    ordinal: int,
    text: str,
) -> RagSectionReadingBlock:
    return RagSectionReadingBlock(
        block_id=block_id,
        section_id="section-current",
        ordinal=ordinal,
        raw_text=text,
        source_spans=(SourceSpan(10 + ordinal * 10, 20 + ordinal * 10),),
        page_labels=("2",),
        anchor_labels=(),
    )

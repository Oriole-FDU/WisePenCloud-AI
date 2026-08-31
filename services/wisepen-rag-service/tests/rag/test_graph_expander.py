import pytest
from common.utils.document import SourceSpan
from common.utils.ranking import RankCandidate, RankedCandidate, RankResult

from rag.application.rag.navigate import (
    KnowledgeGraphExpander,
    UnknownSeedNodeError,
)
from rag.application.rag.navigate.graph_expander import (
    _build_graph_reading_block_views,
    _render_path,
    _to_path_view,
)
from rag.domain.models.acl import PermissionScope
from rag.domain.models.content import ReadingBlock
from rag.domain.models.graph import (
    GraphEvidence,
    KnowledgeMention,
    KnowledgeNode,
    KnowledgeNodeKind,
    KnowledgeRelationType,
)
from rag.domain.models.structure import Section
from rag.domain.repositories.mongo.published_resource_reader import (
    PublishedGraphEvidence,
)
from rag.domain.repositories.neo4j import (
    GraphQuerySubgraph,
    TraversedEdge,
    TraversedPath,
)
from rag.domain.repositories.redis import NavigationState


class _StateStore:
    def __init__(self, *, added=None) -> None:
        self.added = ["node-b"] if added is None else added
        self.calls = []

    async def get(self, state_id):
        return NavigationState(
            state_id=state_id,
            user_id="user-1",
            session_id="session-1",
            known_node_ids=["node-a"],
        )

    async def add_known_nodes(self, **kwargs):
        self.calls.append(kwargs)
        return self.added


class _KnowledgeGraph:
    def __init__(self, paths, mentions=None) -> None:
        self.paths = paths
        self.mentions = [_mention()] if mentions is None else mentions
        self.path_request = None
        self.subgraph_request = None

    async def find_subgraph(self, **kwargs):
        self.subgraph_request = kwargs
        return GraphQuerySubgraph(paths=self.paths, mentions=self.mentions)


class _RankingPipeline:
    def __init__(self) -> None:
        self.request = None

    async def arank(self, request):
        self.request = request
        return RankResult(
            ranked=tuple(
                RankedCandidate(
                    candidate=RankCandidate(candidate_id=candidate.candidate_id),
                    rank=index,
                    score=1.0,
                )
                for index, candidate in enumerate(request.candidates, 1)
            ),
            total_candidates=len(request.candidates),
        )


class _EvidenceVerifier:
    def __init__(self, records) -> None:
        self.records = {
            record.evidence.evidence_id: record for record in records
        }
        self.calls = []

    async def verify(self, evidence):
        self.calls.append(list(evidence))
        return [self.records[item.evidence_id] for item in evidence]


class _Authorizer:
    def __init__(self, denied=()) -> None:
        self.denied = set(denied)

    async def readable_resource_ids(self, resource_ids, *, scope):
        return [
            resource_id
            for resource_id in resource_ids
            if resource_id not in self.denied
        ]


@pytest.mark.asyncio
async def test_expand_returns_relation_and_discovered_node_reading_blocks() -> None:
    relation_record = _record(_relation_evidence())
    mention_record = _record(_mention_evidence())
    knowledge_graph = _KnowledgeGraph([_path()])
    ranking = _RankingPipeline()
    state_store = _StateStore()
    expander = KnowledgeGraphExpander(
        knowledge_graph=knowledge_graph,
        ranking_pipeline=ranking,
        evidence_verifier=_EvidenceVerifier([relation_record, mention_record]),
        authorizer=_Authorizer(),
        state_store=state_store,
    )

    result = await expander.expand(**_request())

    assert ranking.request.query.text == "扩展问题"
    assert knowledge_graph.subgraph_request["seed_node_ids"] == ["node-a"]
    assert knowledge_graph.subgraph_request["mention_limit_per_node"] == 3
    assert state_store.calls[0]["node_ids"] == ["node-b"]
    assert [node.node_id for node in result.discovered_nodes] == ["node-b"]
    assert result.seed_nodes[0].role.value == "seed"
    assert result.discovered_nodes[0].role.value == "discovered"
    assert (
        result.discovered_nodes[0].mention_evidence[0].reading_block_id
        == "block-node"
    )
    assert result.paths[0].path == "Alpha -[DEPENDS_ON]-> Beta"
    assert (
        result.paths[0].relations[0].relation_evidence[0].reading_block_id
        == "block-relation"
    )
    assert (
        result.paths[0].relations[0].relation_evidence[0].quote
        == "Alpha depends on Beta."
    )
    blocks = result.evidence_reading_blocks
    assert {block.reading_block_id for block in blocks} == {
        "block-relation",
        "block-node",
    }
    assert all(not hasattr(block, "matches") for block in blocks)


def test_graph_evidence_blocks_show_all_sections_with_title_boundaries() -> None:
    first = Section(
        section_id="section-1",
        title="第一节",
        level=2,
        parent_section_id="parent",
        ordinal=0,
        section_path=["文档", "第一节"],
        own_span=SourceSpan(0, 2),
        subtree_span=SourceSpan(0, 2),
        content_spans=[SourceSpan(0, 2)],
    )
    second = Section(
        section_id="section-2",
        title="第二节",
        level=2,
        parent_section_id="parent",
        ordinal=1,
        section_path=["文档", "第二节"],
        own_span=SourceSpan(2, 4),
        subtree_span=SourceSpan(2, 4),
        content_spans=[SourceSpan(2, 4)],
    )
    block = ReadingBlock(
        block_id="block-1",
        section_ids=[first.section_id, second.section_id],
        ordinal=0,
        raw_text="甲文\n\n乙文",
        source_spans=[SourceSpan(0, 2), SourceSpan(2, 4)],
    )
    evidence = GraphEvidence(
        evidence_id="evidence-1",
        resource_id="resource-1",
        content_revision="revision-1",
        reading_block_id=block.block_id,
        source_span=SourceSpan(0, 2),
        quote="甲文",
    )
    record = PublishedGraphEvidence(
        evidence=evidence,
        reading_block=block,
        section=first,
        block_range=SourceSpan(0, 2),
        reading_block_sections=[first, second],
    )
    duplicate = PublishedGraphEvidence(
        evidence=GraphEvidence(
            evidence_id="evidence-2",
            resource_id="resource-1",
            content_revision="revision-1",
            reading_block_id=block.block_id,
            source_span=SourceSpan(2, 4),
            quote="乙文",
        ),
        reading_block=block,
        section=second,
        block_range=SourceSpan(4, 6),
        reading_block_sections=[first, second],
    )

    blocks = _build_graph_reading_block_views([record, duplicate])

    assert len(blocks) == 1
    block_view = blocks[0]
    assert block_view.resource_id == "resource-1"
    assert block_view.text == "## 第一节\n\n甲文\n\n## 第二节\n\n乙文"
    assert [section.section_id for section in block_view.sections] == [
        "section-1",
        "section-2",
    ]
    assert all(section.is_complete for section in block_view.sections)


@pytest.mark.asyncio
async def test_expand_drops_path_when_new_node_has_no_mention_evidence() -> None:
    state_store = _StateStore()
    expander = KnowledgeGraphExpander(
        knowledge_graph=_KnowledgeGraph([_path()], mentions=[]),
        ranking_pipeline=_RankingPipeline(),
        evidence_verifier=_EvidenceVerifier([_record(_relation_evidence())]),
        authorizer=_Authorizer(),
        state_store=state_store,
    )

    result = await expander.expand(**_request())

    assert result.paths == []
    assert result.discovered_nodes == []
    assert state_store.calls == []


@pytest.mark.asyncio
async def test_expand_rejects_unknown_seed() -> None:
    expander = KnowledgeGraphExpander(
        knowledge_graph=_KnowledgeGraph([]),
        ranking_pipeline=_RankingPipeline(),
        evidence_verifier=_EvidenceVerifier([]),
        authorizer=_Authorizer(),
        state_store=_StateStore(),
    )

    request = _request()
    request["seed_node_ids"] = ["unknown"]
    with pytest.raises(UnknownSeedNodeError):
        await expander.expand(**request)


@pytest.mark.asyncio
async def test_expand_returns_nothing_when_concurrent_call_added_nodes_first() -> None:
    expander = KnowledgeGraphExpander(
        knowledge_graph=_KnowledgeGraph([_path()]),
        ranking_pipeline=_RankingPipeline(),
        evidence_verifier=_EvidenceVerifier(
            [_record(_relation_evidence()), _record(_mention_evidence())]
        ),
        authorizer=_Authorizer(),
        state_store=_StateStore(added=[]),
    )

    result = await expander.expand(**_request())

    assert result.paths == []
    assert result.discovered_nodes == []
    assert result.evidence_reading_blocks == []


@pytest.mark.asyncio
async def test_expand_filters_paths_revoked_by_local_acl_after_neo4j_query() -> None:
    verifier = _EvidenceVerifier([])
    expander = KnowledgeGraphExpander(
        knowledge_graph=_KnowledgeGraph([_path()]),
        ranking_pipeline=_RankingPipeline(),
        evidence_verifier=verifier,
        authorizer=_Authorizer(denied={"resource-1"}),
        state_store=_StateStore(),
    )

    result = await expander.expand(**_request())

    assert result.paths == []
    assert verifier.calls == []


def test_render_path_preserves_fact_direction_for_reverse_traversal() -> None:
    path = TraversedPath(
        nodes=[_node("node-b", "Beta"), _node("node-a", "Alpha")],
        edges=[_edge(source="node-a", target="node-b")],
    )

    text, relations = _render_path(path)

    assert text == "Beta <-[DEPENDS_ON]- Alpha"
    assert relations == ["Alpha -[DEPENDS_ON]-> Beta"]


def test_render_path_handles_mixed_directions_and_keeps_step_order() -> None:
    path = TraversedPath(
        nodes=[
            _node("node-b", "Beta"),
            _node("node-a", "Alpha"),
            _node("node-c", "Gamma"),
        ],
        edges=[
            _edge(edge_id="edge-1", source="node-a", target="node-b"),
            _edge(
                edge_id="edge-2",
                source="node-a",
                target="node-c",
                relation=KnowledgeRelationType.CAUSES,
            ),
        ],
    )

    text, relations = _render_path(path)

    assert text == "Beta <-[DEPENDS_ON]- Alpha -[CAUSES]-> Gamma"
    assert relations == [
        "Alpha -[DEPENDS_ON]-> Beta",
        "Alpha -[CAUSES]-> Gamma",
    ]


def test_render_path_uses_related_to_predicate_and_ignores_other_predicates() -> None:
    related_path = TraversedPath(
        nodes=[_node("node-a", 'A ("quoted")\nlabel'), _node("node-b", "B")],
        edges=[
            _edge(
                relation=KnowledgeRelationType.RELATED_TO,
                predicate='because "x"\nline',
            )
        ],
    )
    ordinary_path = TraversedPath(
        nodes=[_node("node-a", "A"), _node("node-b", "B")],
        edges=[
            _edge(
                relation=KnowledgeRelationType.CAUSES,
                predicate="must not leak",
            )
        ],
    )

    related_text, _ = _render_path(related_path)
    ordinary_text, _ = _render_path(ordinary_path)

    assert related_text == 'A ("quoted")\nlabel -[because "x"\nline]-> B'
    assert ordinary_text == "A -[CAUSES]-> B"


def test_render_path_rejects_an_edge_that_does_not_join_adjacent_nodes() -> None:
    path = TraversedPath(
        nodes=[_node("node-a", "A"), _node("node-b", "B")],
        edges=[_edge(source="node-x", target="node-y")],
    )

    with pytest.raises(RuntimeError, match="does not connect adjacent"):
        _render_path(path)


def test_path_view_preserves_each_graph_evidence_identity() -> None:
    first = _record(
        _evidence("evidence-1", "block-1", "first quote")
    )
    second = _record(
        _evidence("evidence-2", "block-2", "same quote")
    )
    edge = _edge(evidence=[first.evidence, second.evidence])
    path = TraversedPath(
        nodes=[_node("node-a", "A"), _node("node-b", "B")],
        edges=[edge],
    )

    view, retained = _to_path_view(path, {edge.edge_id: [first, second]})

    assert [item.reading_block_id for item in view.relations[0].relation_evidence] == [
        "block-1",
        "block-2",
    ]
    assert retained == [first, second]


def _request() -> dict:
    return dict(
        state_id="nav-1",
        session_id="session-1",
        permission_scope=PermissionScope(user_id="user-1"),
        seed_node_ids=["node-a"],
        query="扩展问题",
    )


def _path() -> TraversedPath:
    return TraversedPath(
        nodes=[_node("node-a", "Alpha"), _node("node-b", "Beta")],
        edges=[_edge()],
    )


def _node(node_id: str, label: str) -> KnowledgeNode:
    return KnowledgeNode(
        node_id=node_id,
        kind=KnowledgeNodeKind.ENTITY,
        label=label,
    )


def _edge(
    *,
    edge_id: str = "edge-1",
    source: str = "node-a",
    target: str = "node-b",
    relation: KnowledgeRelationType = KnowledgeRelationType.DEPENDS_ON,
    predicate: str | None = None,
    evidence: list[GraphEvidence] | None = None,
) -> TraversedEdge:
    return TraversedEdge(
        edge_id=edge_id,
        source_node_id=source,
        target_node_id=target,
        relation_type=relation,
        evidence=[_relation_evidence()] if evidence is None else evidence,
        predicate=predicate,
    )


def _mention() -> KnowledgeMention:
    return KnowledgeMention(
        mention_id="mention-node-b",
        node_id="node-b",
        evidence=_mention_evidence(),
    )


def _relation_evidence() -> GraphEvidence:
    return _evidence(
        "evidence-relation",
        "block-relation",
        "Alpha depends on Beta.",
    )


def _mention_evidence() -> GraphEvidence:
    return _evidence("evidence-node", "block-node", "Beta")


def _evidence(evidence_id: str, block_id: str, quote: str) -> GraphEvidence:
    return GraphEvidence(
        evidence_id=evidence_id,
        resource_id="resource-1",
        content_revision="revision-1",
        reading_block_id=block_id,
        source_span=SourceSpan(0, len(quote)),
        quote=quote,
    )


def _record(evidence: GraphEvidence) -> PublishedGraphEvidence:
    span = SourceSpan(0, len(evidence.quote))
    section_id = f"section-{evidence.reading_block_id}"
    section = Section(
        section_id=section_id,
        title="测试章节",
        level=1,
        parent_section_id=None,
        ordinal=0,
        section_path=["测试章节"],
        own_span=span,
        subtree_span=span,
        content_spans=[span],
    )
    return PublishedGraphEvidence(
        evidence=evidence,
        reading_block=ReadingBlock(
            block_id=evidence.reading_block_id,
            section_ids=[section_id],
            ordinal=0,
            raw_text=evidence.quote,
            source_spans=[span],
            page_labels=["1"],
        ),
        section=section,
        block_range=span,
        reading_block_sections=[section],
    )

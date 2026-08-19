from dataclasses import dataclass, replace

import pytest
from common.utils.document import SourceSpan
from common.utils.ranking import RankDecision, RankedCandidate, RankResult

from rag.application.rag.acl import PermissionAuthorizer
from rag.application.rag.navigate import (
    ReadingCandidateLocator,
    SourceEvidenceVerifier,
)
from rag.application.rag.navigate.candidate_locator import (
    _build_retrieval_reading_block_views,
)
from rag.domain.models.acl import PermissionScope, ResourceAcl
from rag.domain.models.content import ReadingBlock
from rag.domain.models.graph import KnowledgeNode, KnowledgeNodeKind
from rag.domain.models.provenance import SourceEvidence, SourceRef
from rag.domain.models.retrieval import RetrievalCandidate
from rag.domain.models.structure import Section
from rag.domain.repositories.redis import NavigationState


@dataclass
class _EmbeddingResult:
    embeddings: list[list[float]]


class _EmbeddingClient:
    async def aembed(self, values):
        return _EmbeddingResult([[0.1, 0.2]])


class _CandidateSearch:
    def __init__(self, candidates):
        self.candidates = candidates

    async def search(self, **kwargs):
        return self.candidates


class _AclReader:
    def __init__(self, denied=()) -> None:
        self.denied = set(denied)

    async def get_resource_acls(self, resource_ids):
        return {
            resource_id: ResourceAcl(
                resource_id=resource_id,
                acl_revision=1,
                owner_id="user-1",
            )
            for resource_id in resource_ids
            if resource_id not in self.denied
        }


class _RevokingAclReader(_AclReader):
    """允许初次召回过滤，但在证据核验后撤销本地读取权限。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def get_resource_acls(self, resource_ids):
        self.calls += 1
        if self.calls == 1:
            return await super().get_resource_acls(resource_ids)
        return {}


class _RevisionReader:
    async def get_content_revision(self, resource_id):
        return "revision-1"


class _PublishedResourceReader:
    def __init__(self, records):
        self.records = records
        self.calls = []

    async def get_source_evidence(
        self,
        resource_id,
        content_revision,
        source_ref_ids,
    ):
        self.calls.append(list(source_ref_ids))
        return {ref_id: self.records[ref_id] for ref_id in source_ref_ids}


class _RankingPipeline:
    def __init__(self, ranked_ids, decision=RankDecision.RELEVANT):
        self.ranked_ids = ranked_ids
        self.decision = decision

    async def arank(self, request):
        candidates = {item.candidate_id: item for item in request.candidates}
        return RankResult(
            ranked=tuple(
                RankedCandidate(
                    candidate=candidates[candidate_id],
                    rank=index,
                    score=1.0,
                )
                for index, candidate_id in enumerate(self.ranked_ids, 1)
            ),
            total_candidates=len(candidates),
            decision=self.decision,
        )


class _KnowledgeGraph:
    def __init__(self, nodes=None) -> None:
        self.nodes = (
            [
                KnowledgeNode(
                    node_id="node-1",
                    kind=KnowledgeNodeKind.ENTITY,
                    label="主题",
                )
            ]
            if nodes is None
            else nodes
        )
        self.request = None

    async def find_nodes(self, **kwargs):
        self.request = kwargs
        return self.nodes


class _StateStore:
    def __init__(self):
        self.created = None

    async def create(self, **kwargs):
        self.created = kwargs
        return NavigationState(state_id="nav-1", **kwargs)


def _section() -> Section:
    return Section(
        section_id="section-1",
        title="当前标题",
        level=3,
        parent_section_id=None,
        ordinal=0,
        section_path=["很长的父标题", "当前标题"],
        own_span=SourceSpan(0, 10),
        subtree_span=SourceSpan(0, 10),
        content_spans=[SourceSpan(0, 10)],
    )


def _candidate(chunk_id, span, *, block_id="block-1") -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        reading_block_id=block_id,
        section_id="section-1",
        section_path=["很长的父标题", "当前标题"],
        resource_id="resource-1",
        content_revision="revision-1",
        raw_text="abcdefghij"[span.start_offset : span.end_offset],
        source_spans=[span],
        page_labels=["1"],
        anchor_labels=[],
        source_ref_id=f"ref-{chunk_id}",
        score=0.8,
    )


def _record(candidate) -> SourceEvidence:
    section = _section()
    return SourceEvidence(
        source_ref=SourceRef(
            ref_id=candidate.source_ref_id,
            resource_id=candidate.resource_id,
            content_revision=candidate.content_revision,
            chunk_id=candidate.chunk_id,
            reading_block_id=candidate.reading_block_id,
            section_id=candidate.section_id,
            section_path=list(candidate.section_path),
            source_spans=list(candidate.source_spans),
            page_labels=list(candidate.page_labels),
            anchor_labels=[],
        ),
        reading_block=ReadingBlock(
            block_id=candidate.reading_block_id,
            section_ids=[candidate.section_id],
            ordinal=0,
            raw_text="abcdefghij",
            source_spans=[SourceSpan(0, 10)],
            page_labels=["1"],
        ),
        section=section,
        source_text=candidate.raw_text,
        reading_block_sections=[section],
    )


def _candidate_id(candidate) -> str:
    return (
        f"{candidate.resource_id}\0{candidate.content_revision}\0{candidate.chunk_id}"
    )


def _locator(
    candidates,
    *,
    ranked_ids,
    decision=RankDecision.RELEVANT,
    acl_reader=None,
    knowledge_graph=None,
    graph_enabled=True,
):
    records = {candidate.source_ref_id: _record(candidate) for candidate in candidates}
    state_store = _StateStore()
    source_evidence_reader = _PublishedResourceReader(records)
    locator = ReadingCandidateLocator(
        embedding_client=_EmbeddingClient(),
        candidate_search=_CandidateSearch(candidates),
        ranking_pipeline=_RankingPipeline(ranked_ids, decision),
        authorizer=PermissionAuthorizer(local_store=acl_reader or _AclReader()),
        evidence_verifier=SourceEvidenceVerifier(reader=source_evidence_reader),
        knowledge_graph=knowledge_graph or _KnowledgeGraph(),
        published_resources=_RevisionReader(),
        state_store=state_store,
        graph_enabled=graph_enabled,
    )
    return locator, state_store, source_evidence_reader


@pytest.mark.asyncio
async def test_locate_omits_graph_nodes_when_graph_is_disabled() -> None:
    candidate = _candidate("chunk-1", SourceSpan(1, 4))
    graph = _KnowledgeGraph()
    locator, state_store, _ = _locator(
        [candidate],
        ranked_ids=[_candidate_id(candidate)],
        knowledge_graph=graph,
        graph_enabled=False,
    )

    result = await locator.locate(
        session_id="session-1",
        semantic_query="问题",
        permission_scope=PermissionScope(user_id="user-1"),
    )

    assert result.nodes is None
    assert graph.request is None
    assert state_store.created["known_node_ids"] == []


@pytest.mark.asyncio
async def test_locate_promotes_chunks_to_one_block_with_minimal_match_anchors() -> None:
    first = _candidate("chunk-1", SourceSpan(1, 4))
    second = _candidate("chunk-2", SourceSpan(6, 9))
    locator, state_store, _ = _locator(
        [first, second],
        ranked_ids=[_candidate_id(first), _candidate_id(second)],
    )

    result = await locator.locate(
        session_id="session-1",
        semantic_query="问题",
        permission_scope=PermissionScope(user_id="user-1"),
        max_results=1,
    )

    assert result.retrieval_status is RankDecision.RELEVANT
    block = result.reading_blocks[0]
    assert block.resource_id == "resource-1"
    assert block.text == "abcdefghij"
    assert block.page_labels == ["1"]
    assert block.sections[0].title == "当前标题"
    assert block.sections[0].section_path == "很长的父标题 > 当前标题"
    assert result.nodes[0].resource_id is None
    assert state_store.created["known_node_ids"] == ["node-1"]
    assert "known_sections" not in state_store.created


@pytest.mark.asyncio
async def test_locate_seeds_graph_from_full_promoted_block() -> None:
    first = _candidate("chunk-1", SourceSpan(1, 4))
    second = _candidate("chunk-2", SourceSpan(6, 9))
    graph = _KnowledgeGraph()
    locator, _, _ = _locator(
        [first, second],
        ranked_ids=[_candidate_id(first), _candidate_id(second)],
        knowledge_graph=graph,
    )

    await locator.locate(
        session_id="session-1",
        semantic_query="问题",
        permission_scope=PermissionScope(user_id="user-1"),
        max_results=1,
    )

    blocks = graph.request["reading_blocks"]
    assert len(blocks) == 1
    assert blocks[0].reading_block_id == "block-1"
    assert blocks[0].matched_source_spans == [
        SourceSpan(1, 4),
        SourceSpan(6, 9),
    ]


def test_flat_text_retrieval_view_keeps_synthetic_section_context() -> None:
    candidate = _candidate("chunk-1", SourceSpan(0, 4))
    record = _record(candidate)
    record = replace(
        record,
        section=replace(
            record.section,
            title="全文片段 1",
            section_path=("全文片段 1",),
        ),
    )
    record = replace(record, reading_block_sections=[record.section])

    block = _build_retrieval_reading_block_views([record])[0]

    assert block.sections[0].title == "全文片段 1"
    assert block.sections[0].section_path == "全文片段 1"
    assert block.page_labels == ["1"]
    assert block.sections[0].block_is_enough is True


def test_reading_block_section_completeness_follows_own_span_coverage() -> None:
    # 部分覆盖：block 只覆盖直属正文前半段。
    partial = _record(_candidate("chunk-1", SourceSpan(0, 4)))
    partial = replace(
        partial,
        section=replace(partial.section, content_spans=(SourceSpan(0, 10),)),
    )
    partial = replace(partial, reading_block_sections=[partial.section])
    partial.reading_block.source_spans = [SourceSpan(0, 5)]

    block = _build_retrieval_reading_block_views([partial])[0]

    assert block.sections[0].block_is_enough is False

    # 完整性属于单个 ReadingBlock，不跨多个返回 block 聚合。
    first = _record(_candidate("chunk-1", SourceSpan(0, 4)))
    first = replace(
        first,
        section=replace(first.section, content_spans=(SourceSpan(0, 10),)),
    )
    first = replace(first, reading_block_sections=[first.section])
    first.reading_block.source_spans = [SourceSpan(0, 5)]
    second = _record(_candidate("chunk-2", SourceSpan(6, 9), block_id="block-2"))
    second = replace(
        second,
        section=first.section,
        reading_block_sections=[first.section],
    )
    second.reading_block.source_spans = [SourceSpan(5, 10)]

    blocks = _build_retrieval_reading_block_views([first, second])

    assert all(block.sections[0].block_is_enough is False for block in blocks)



def test_multisection_block_shows_title_boundaries_and_all_sections() -> None:
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
    record = SourceEvidence(
        source_ref=SourceRef(
            ref_id="ref-1",
            resource_id="resource-1",
            content_revision="revision-1",
            chunk_id="chunk-1",
            reading_block_id=block.block_id,
            section_id=first.section_id,
            section_path=list(first.section_path),
        ),
        reading_block=block,
        section=first,
        source_text="甲文",
        reading_block_sections=[first, second],
    )
    duplicate = replace(
        record,
        source_ref=replace(
            record.source_ref,
            ref_id="ref-2",
            chunk_id="chunk-2",
            section_id=second.section_id,
            section_path=list(second.section_path),
        ),
        section=second,
        source_text="乙文",
    )

    blocks = _build_retrieval_reading_block_views([record, duplicate])

    assert len(blocks) == 1
    block_view = blocks[0]
    assert block_view.resource_id == "resource-1"
    assert block_view.text == "## 第一节\n\n甲文\n\n## 第二节\n\n乙文"
    assert [section.section_id for section in block_view.sections] == [
        "section-1",
        "section-2",
    ]
    assert all(section.block_is_enough for section in block_view.sections)


@pytest.mark.asyncio
async def test_locate_max_results_counts_blocks_not_chunks() -> None:
    first = _candidate("chunk-1", SourceSpan(0, 4), block_id="block-1")
    duplicate = _candidate("chunk-2", SourceSpan(4, 6), block_id="block-1")
    other = _candidate("chunk-3", SourceSpan(6, 10), block_id="block-2")
    locator, _, source_evidence_reader = _locator(
        [first, duplicate, other],
        ranked_ids=[
            _candidate_id(first),
            _candidate_id(duplicate),
            _candidate_id(other),
        ],
    )

    result = await locator.locate(
        session_id="session-1",
        semantic_query="问题",
        permission_scope=PermissionScope(user_id="user-1"),
        max_results=1,
    )

    assert [block.reading_block_id for block in result.reading_blocks] == ["block-1"]
    assert source_evidence_reader.calls == [["ref-chunk-1", "ref-chunk-2"]]


@pytest.mark.asyncio
async def test_locate_irrelevant_result_still_creates_graph_state() -> None:
    candidate = _candidate("chunk-1", SourceSpan(0, 4))
    locator, state_store, source_evidence_reader = _locator(
        [candidate],
        ranked_ids=[],
        decision=RankDecision.IRRELEVANT,
    )

    result = await locator.locate(
        session_id="session-1",
        semantic_query="不相关",
        permission_scope=PermissionScope(user_id="user-1"),
    )

    assert result.retrieval_status is RankDecision.IRRELEVANT
    assert result.reading_blocks == []
    assert source_evidence_reader.calls == []
    assert state_store.created["known_node_ids"] == []


@pytest.mark.asyncio
async def test_locate_filters_resource_node_revoked_after_neo4j_query() -> None:
    candidate = _candidate("chunk-1", SourceSpan(0, 4))
    graph = _KnowledgeGraph(
        nodes=[
            KnowledgeNode(
                node_id="resource-node-2",
                kind=KnowledgeNodeKind.RESOURCE,
                label="受限资源",
                resource_id="resource-2",
            )
        ]
    )
    locator, state_store, _ = _locator(
        [candidate],
        ranked_ids=[_candidate_id(candidate)],
        acl_reader=_AclReader(denied={"resource-2"}),
        knowledge_graph=graph,
    )

    result = await locator.locate(
        session_id="session-1",
        semantic_query="问题",
        permission_scope=PermissionScope(user_id="user-1"),
    )

    assert result.nodes == []
    assert state_store.created["known_node_ids"] == []


@pytest.mark.asyncio
async def test_locate_drops_evidence_revoked_after_authoritative_read() -> None:
    candidate = _candidate("chunk-1", SourceSpan(0, 4))
    locator, state_store, _ = _locator(
        [candidate],
        ranked_ids=[_candidate_id(candidate)],
        acl_reader=_RevokingAclReader(),
        knowledge_graph=_KnowledgeGraph(nodes=[]),
    )

    result = await locator.locate(
        session_id="session-1",
        semantic_query="问题",
        permission_scope=PermissionScope(user_id="user-1"),
    )

    assert result.reading_blocks == []
    assert result.nodes == []
    assert state_store.created["known_node_ids"] == []

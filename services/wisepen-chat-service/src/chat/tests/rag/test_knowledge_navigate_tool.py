from __future__ import annotations

from typing import Any

import pytest

from chat.application.rag.evidence import RagMaterializedHit, RagMaterializedSource
from chat.application.rag.graph_extraction import (
    KnowledgeEntityType,
    KnowledgeNodeKind,
    KnowledgeRelationProfile,
    KnowledgeRelationType,
)
from chat.application.rag.knowledge_navigation import (
    KnowledgeNavigationEdge,
    KnowledgeNavigationNode,
    KnowledgeNavigationPath,
    KnowledgeNavigationService,
    KnowledgeNavigationState,
)
from chat.application.rag.ingestion import (
    RagSectionNode,
    RagSectionReadingBlock,
    RagSourceRef,
)
from chat.application.rag.retrieval import (
    RagRankedHit,
    RagRetrievalCandidate,
)
from chat.application.rag.section_navigation import RagLocatedSection, RagSectionView
from chat.application.tools.core import ToolExecutionError
from chat.application.tools.rag_tools import (
    KnowledgeNavigateExpandTool,
    KnowledgeNavigateLocateTool,
    KnowledgeNavigateSectionsTool,
)
from chat.application.utils.chunkers import SourceSpan
from chat.application.utils.ranking import RankCandidate, RankedCandidate
from common.core.domain import GroupRoleType


class _Locator:
    def __init__(self, hits: tuple[RagLocatedSection, ...] = ()) -> None:
        self.hits = hits
        self.requests = []

    async def locate(self, request):
        self.requests.append(request)
        return self.hits


class _StateRepository:
    def __init__(self) -> None:
        self.state: KnowledgeNavigationState | None = None
        self.created: list[dict[str, Any]] = []
        self.added: list[tuple[str, tuple[str, ...]]] = []

    async def create(self, **kwargs):
        self.created.append(kwargs)
        self.state = KnowledgeNavigationState(state_id="kns_test", **kwargs)
        return self.state

    async def get(self, state_id):
        if self.state is not None and self.state.state_id == state_id:
            return self.state
        return None

    async def add_known_nodes(self, *, state_id, node_ids):
        if self.state is None or self.state.state_id != state_id:
            return False
        self.added.append((state_id, node_ids))
        self.state = KnowledgeNavigationState(
            state_id=self.state.state_id,
            user_id=self.state.user_id,
            session_id=self.state.session_id,
            root_query=self.state.root_query,
            known_node_ids=tuple(
                dict.fromkeys((*self.state.known_node_ids, *node_ids))
            ),
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
    def __init__(self, sources=()) -> None:
        self.sources = sources

    async def materialize_refs(self, request, permission_scope):
        return self.sources


class _SectionNavigator:
    def __init__(self, *, read_views=(), source_views=()) -> None:
        self.read_views = read_views
        self.source_views = source_views
        self.read_requests = []

    async def read_sections(self, **kwargs):
        self.read_requests.append(kwargs)
        return self.read_views

    async def build_sources(self, sources):
        return self.source_views


class _PermissionAuthorizer:
    async def accessible_resource_ids(self, resource_ids, scope):
        return frozenset(resource_ids)


def test_tool_schemas_keep_three_navigation_boundaries_separate() -> None:
    service = _service()
    locate = KnowledgeNavigateLocateTool(service=service)
    sections = KnowledgeNavigateSectionsTool(service=service)
    expand = KnowledgeNavigateExpandTool(service=service)

    assert set(locate.definition.llm_spec.parameters_schema.raw["properties"]) == {
        "query",
        "max_results",
    }
    assert set(sections.definition.llm_spec.parameters_schema.raw["properties"]) == {
        "state_id",
        "resource_id",
        "section_ids",
    }
    assert set(expand.definition.llm_spec.parameters_schema.raw["properties"]) == {
        "state_id",
        "node_ids",
        "query",
        "relation_types",
        "direction",
        "max_depth",
        "max_results",
    }
    for tool in (locate, sections, expand):
        schema = tool.definition.llm_spec.parameters_schema.raw
        assert schema["additionalProperties"] is False
        assert "oneOf" not in schema
        assert tool.definition.preflight_hooks == ()
        assert all(item.get("description") for item in schema["properties"].values())


@pytest.mark.asyncio
async def test_locate_returns_section_view_and_registers_tree_frontier() -> None:
    located = _located_section()
    locator = _Locator((located,))
    state_repository = _StateRepository()
    graph_repository = _GraphRepository(
        mentions=(_entity_node("kn_alpha", "Alpha"),)
    )
    tool = KnowledgeNavigateLocateTool(
        service=_service(
            locator=locator,
            graph_repository=graph_repository,
            state_repository=state_repository,
        )
    )

    result = await tool.execute(
        {
            "user_id": "user-1",
            "session_id": "session-1",
            "group_role_map": {"group-1": GroupRoleType.MEMBER},
        },
        query="  核心概念是什么？  ",
        max_results=2,
    )

    visible = result.visible_result
    section = visible["sources"][0]
    assert section["section_id"] == "section-current"
    assert section["summary"] == "核心概念摘要"
    assert section["frontier"]["parent"]["section_id"] == "section-root"
    assert section["frontier"]["previous"]["section_id"] == "section-previous"
    assert section["frontier"]["children"][0]["section_id"] == "section-child"
    assert "content_index" not in section["frontier"]["parent"]
    assert section["reading_blocks"][0]["reading_block_id"] == "block-1"
    assert result.cacheable_texts[section["reading_blocks"][0]["content_index"]].text == "本节命中附近的完整阅读块。"
    assert result.cacheable_texts[section["evidence"][0]["content_index"]].text == "核心概念的直接证据。"
    assert state_repository.created[0]["known_node_ids"] == (
        "kn_alpha",
        "section-current",
        "section-root",
        "section-previous",
        "section-child",
    )
    assert locator.requests[0].query == "核心概念是什么？"


@pytest.mark.asyncio
async def test_sections_tool_reads_selected_node_and_adds_new_frontier() -> None:
    located = _located_section()
    view = located.view
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
        known_node_ids=("section-current",),
    )
    navigator = _SectionNavigator(read_views=(view,))
    tool = KnowledgeNavigateSectionsTool(
        service=_service(
            section_navigator=navigator,
            state_repository=state_repository,
        )
    )

    result = await tool.execute(
        {
            "user_id": "user-1",
            "session_id": "session-1",
            "group_role_map": {},
        },
        state_id="kns_test",
        resource_id="resource-1",
        section_ids=["section-current"],
    )

    section = result.visible_result["sections"][0]
    assert [item["reading_block_id"] for item in section["reading_blocks"]] == [
        "block-1",
        "block-2",
    ]
    assert navigator.read_requests == [
        {
            "resource_id": "resource-1",
            "section_ids": ("section-current",),
        }
    ]
    assert state_repository.added == [
        (
            "kns_test",
            ("section-root", "section-previous", "section-next", "section-child"),
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
        known_node_ids=("kn_alpha",),
    )
    edge = KnowledgeNavigationEdge(
        edge_id="kne_1",
        source_node_id="kn_alpha",
        target_node_id="kn_beta",
        relation_type=KnowledgeRelationType.DEPENDS_ON,
        relation_profile=KnowledgeRelationProfile.CORE,
        predicate=None,
        evidence_resource_id="resource-1",
        evidence_ref_ids=("knev_1",),
        evidence_source_ref_ids=("source-1",),
        source_content_revision="revision-3",
        relation_revision="relation-3",
    )
    path = KnowledgeNavigationPath(
        nodes=(
            _entity_node("kn_alpha", "Alpha"),
            _entity_node("kn_beta", "Beta"),
        ),
        edges=(edge,),
    )
    graph_repository = _GraphRepository(paths=(path,))
    tool = KnowledgeNavigateExpandTool(
        service=_service(
            graph_repository=graph_repository,
            evidence_materializer=_EvidenceMaterializer(
                (located.materialized_hit.source,)
            ),
            section_navigator=_SectionNavigator(source_views=(located.view,)),
            state_repository=state_repository,
        )
    )

    result = await tool.execute(
        {
            "user_id": "user-1",
            "session_id": "session-1",
            "group_role_map": {},
        },
        state_id="kns_test",
        node_ids=["kn_alpha"],
        relation_types=["DEPENDS_ON"],
    )

    assert result.visible_result["edges"][0]["direction"] == "out"
    assert result.visible_result["sources"][0]["section_id"] == "section-current"
    assert state_repository.added == [("kns_test", ("kn_beta",))]


@pytest.mark.asyncio
async def test_sections_tool_rejects_unknown_section() -> None:
    state_repository = _StateRepository()
    state_repository.state = KnowledgeNavigationState(
        state_id="kns_test",
        user_id="user-1",
        session_id="session-1",
        root_query="Alpha",
        known_node_ids=("section-current",),
    )
    tool = KnowledgeNavigateSectionsTool(
        service=_service(state_repository=state_repository)
    )

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.execute(
            {
                "user_id": "user-1",
                "session_id": "session-1",
                "group_role_map": {},
            },
            state_id="kns_test",
            resource_id="resource-1",
            section_ids=["section-unknown"],
        )

    assert exc_info.value.reason == "knowledge_navigation_state_invalidated"


def _service(
    *,
    locator=None,
    graph_repository=None,
    evidence_materializer=None,
    section_navigator=None,
    state_repository=None,
) -> KnowledgeNavigationService:
    return KnowledgeNavigationService(
        locator=locator or _Locator(),
        permission_authorizer=_PermissionAuthorizer(),
        graph_repository=graph_repository or _GraphRepository(),
        evidence_materializer=evidence_materializer or _EvidenceMaterializer(),
        section_navigator=section_navigator or _SectionNavigator(),
        state_repository=state_repository or _StateRepository(),
    )


def _entity_node(node_id: str, label: str) -> KnowledgeNavigationNode:
    return KnowledgeNavigationNode(
        node_id=node_id,
        kind=KnowledgeNodeKind.ENTITY,
        label=label,
        entity_type=KnowledgeEntityType.CONCEPT,
        type_tags=(KnowledgeEntityType.CONCEPT.value,),
    )


def _located_section() -> RagLocatedSection:
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
    candidate = RagRetrievalCandidate(
        chunk_id="chunk-1",
        reading_block_id="block-1",
        section_id="section-current",
        section_path=("课程", "核心概念"),
        resource_id="resource-1",
        document_version=3,
        content_revision="revision-3",
        raw_text=source.content,
        page_labels=("2",),
        anchor_labels=("Definition 1",),
        source_ref_id="source-1",
        signals=(),
    )
    materialized = RagMaterializedHit(
        hit=RagRankedHit(
            candidate=candidate,
            ranking=RankedCandidate(
                candidate=RankCandidate(candidate_id="chunk-1"),
                rank=1,
                score=1.0,
            ),
        ),
        reading_block=_reading_block(
            "block-1",
            0,
            "本节命中附近的完整阅读块。",
        ),
        source=source,
    )
    current = _section(
        "section-current",
        "核心概念",
        parent_section_id="section-root",
        ordinal=1,
        summary="核心概念摘要",
    )
    return RagLocatedSection(
        materialized_hit=materialized,
        view=RagSectionView(
            section=current,
            sources=(source,),
            reading_blocks=(materialized.reading_block,),
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

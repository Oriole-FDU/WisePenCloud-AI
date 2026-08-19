import json
from types import SimpleNamespace

import pytest
from common.core.domain import GroupRoleType
from common.core.exceptions import ServiceException
from common.security import SecurityContextHolder
from common.utils.ranking import RankDecision

from rag.api.endpoints.expand import expand_graph, expand_section
from rag.api.endpoints.locate import locate_candidate
from rag.api.schemas import (
    CandidateLocateRequest,
    GraphExpandRequest,
    SectionExpandRequest,
)
from rag.application.rag.navigate import (
    DiscoveredKnowledgeNodeView,
    GraphEvidenceRefView,
    GraphExpandResult,
    GraphNodeRole,
    GraphNodeView,
    GraphPathView,
    GraphReadingBlockView,
    GraphRelationEndpointView,
    GraphRelationView,
    LocateError,
    LocateResult,
    RetrievalReadingBlockView,
    SectionChildrenExpandResult,
    UnknownSeedNodeError,
)
from rag.application.rag.read.content import SectionContentView
from rag.domain.error_codes import RagErrorCode
from rag.domain.models.graph import (
    KnowledgeNode,
    KnowledgeNodeKind,
)
from rag.domain.models.section_navigation import SectionDirection


class _Locator:
    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.request = None

    async def locate(self, **kwargs):
        self.request = SimpleNamespace(**kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class _Expander:
    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.request = None

    async def expand(self, **kwargs):
        self.request = SimpleNamespace(**kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def _source() -> RetrievalReadingBlockView:
    return RetrievalReadingBlockView(
        resource_id="resource-1",
        reading_block_id="block-1",
        text="完整正文",
        page_labels=["1", "2"],
    )


def _graph_source() -> GraphReadingBlockView:
    return GraphReadingBlockView(
        resource_id="resource-1",
        reading_block_id="block-1",
        text="完整正文",
        page_labels=["1", "2"],
    )


@pytest.mark.asyncio
async def test_locate_endpoint_uses_authenticated_identity_and_compact_contract() -> (
    None
):
    locator = _Locator(
        LocateResult(
            state_id="state-1",
            retrieval_status=RankDecision.RELEVANT,
            nodes=[
                KnowledgeNode(
                    node_id="node-1",
                    label="主题",
                    kind=KnowledgeNodeKind.ENTITY,
                )
            ],
            reading_blocks=[_source()],
        )
    )
    SecurityContextHolder.set_group_role_map('{"group-1": 1}')

    response = await locate_candidate(
        CandidateLocateRequest(session_id="session-1", semantic_query="问题"),
        user_id="trusted-user",
        locator=locator,
    )

    assert locator.request.permission_scope.user_id == "trusted-user"
    assert locator.request.permission_scope.group_roles == {
        "group-1": GroupRoleType.ADMIN
    }
    payload = response.data.model_dump(mode="json")
    assert payload["retrieval_status"] == "relevant"
    assert payload["reading_blocks"][0]["resource_id"] == "resource-1"
    assert payload["reading_blocks"][0]["text"] == "完整正文"
    assert payload["reading_blocks"][0]["page_labels"] == ["1", "2"]
    assert "sections" not in payload
    assert "decision" not in payload


@pytest.mark.asyncio
async def test_graph_endpoint_returns_llm_readable_paths_and_evidence_blocks() -> None:
    expander = _Expander(
        GraphExpandResult(
            state_id="state-1",
            discovered_nodes=[
                DiscoveredKnowledgeNodeView(
                    "node-2",
                    "Beta",
                    KnowledgeNodeKind.ENTITY,
                    mention_evidence=[
                        GraphEvidenceRefView(
                            resource_id="resource-1",
                            reading_block_id="block-1",
                            quote="正文",
                        )
                    ],
                ),
            ],
            seed_nodes=[
                GraphNodeView(
                    "node-1",
                    "Alpha",
                    KnowledgeNodeKind.ENTITY,
                    role=GraphNodeRole.SEED,
                )
            ],
            paths=[
                GraphPathView(
                    path="Alpha -[DEPENDS_ON]-> Beta",
                    relations=[
                        GraphRelationView(
                            source=GraphRelationEndpointView("node-1", "Alpha"),
                            predicate="DEPENDS_ON",
                            target=GraphRelationEndpointView("node-2", "Beta"),
                            relation_evidence=[
                                GraphEvidenceRefView(
                                    resource_id="resource-1",
                                    reading_block_id="block-1",
                                    quote="正文",
                                )
                            ],
                        )
                    ],
                )
            ],
            evidence_reading_blocks=[_graph_source()],
        )
    )

    response = await expand_graph(
        GraphExpandRequest(
            session_id="session-1",
            state_id="state-1",
            seed_node_ids=["node-1"],
            query="扩展问题",
        ),
        user_id="user-1",
        expander=expander,
    )

    payload = response.data.model_dump(mode="json")
    assert payload["paths"][0]["path"] == "Alpha -[DEPENDS_ON]-> Beta"
    assert payload["paths"][0]["relations"][0]["relation_evidence"] == [
        {
            "resource_id": "resource-1",
            "reading_block_id": "block-1",
            "quote": "正文",
        }
    ]
    assert payload["seed_nodes"][0]["role"] == "seed"
    assert payload["discovered_nodes"][0]["role"] == "discovered"
    assert payload["discovered_nodes"][0]["mention_evidence"][0][
        "reading_block_id"
    ] == ("block-1")
    graph_block = payload["evidence_reading_blocks"][0]
    assert graph_block["page_labels"] == ["1", "2"]
    assert "evidence_sections" not in payload
    serialized = json.dumps(payload)
    assert "chunk_id" not in serialized
    assert "source_ref" not in serialized
    assert "matches" not in serialized
    assert "nodes" not in payload
    assert "edges" not in payload
    assert "sources" not in payload
    assert "edge_ids" not in payload["paths"][0]
    assert "text" not in payload["paths"][0]
    assert "steps" not in payload["paths"][0]
    assert "node_ids" not in payload["paths"][0]


@pytest.mark.asyncio
async def test_section_endpoint_returns_directional_expansion_with_budget_state() -> (
    None
):
    expander = _Expander(
        SectionChildrenExpandResult(
            from_section_id="section-1",
            sections=[
                SectionContentView(
                    section_id="section-2",
                    title="子标题",
                    section_path="标题 > 子标题",
                    text="子标题正文",
                    allowed_directions=[SectionDirection.PARENT],
                )
            ],
            has_more=True,
            next_after_section_id="section-2",
            budget_exhausted=True,
        )
    )

    response = await expand_section(
        SectionExpandRequest(
            resource_id="resource-1",
            section_id="section-1",
            direction=SectionDirection.CHILDREN,
            char_budget=100,
        ),
        user_id="user-1",
        expander=expander,
    )

    assert expander.request.direction is SectionDirection.CHILDREN
    assert response.data.sections[0].section_id == "section-2"
    assert response.data.has_more is True
    assert response.data.next_after_section_id == "section-2"
    assert response.data.budget_exhausted is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "payload", "dependency_name", "error", "expected"),
    [
        (
            locate_candidate,
            CandidateLocateRequest(session_id="session-1", semantic_query="问题"),
            "locator",
            LocateError("invalid"),
            RagErrorCode.NAVIGATION_INVALID,
        ),
        (
            expand_graph,
            GraphExpandRequest(
                session_id="session-1",
                state_id="state-1",
                seed_node_ids=["node-1"],
                query="扩展问题",
            ),
            "expander",
            UnknownSeedNodeError("node-1"),
            RagErrorCode.NAVIGATION_INVALID,
        ),
    ],
)
async def test_navigation_error_mapping(
    endpoint,
    payload,
    dependency_name,
    error,
    expected,
) -> None:
    dependency = {
        "locator": _Locator,
        "expander": _Expander,
    }[dependency_name](error=error)

    with pytest.raises(ServiceException) as raised:
        await endpoint(
            payload,
            user_id="user-1",
            **{dependency_name: dependency},
        )

    assert raised.value.code == expected.code

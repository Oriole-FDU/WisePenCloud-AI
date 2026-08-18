import pytest
from neo4j_graphrag.experimental.components.types import (
    Neo4jGraph,
    Neo4jNode,
    Neo4jRelationship,
)

from rag.application.rag.index.graph.candidate_validator import (
    KnowledgeCandidateValidator,
)
from rag.application.rag.index.graph.extractor import (
    KnowledgeGraphExtractor, _encode_candidate_graph,
)
from rag.application.rag.index.graph.llm import QueryClientGraphRagLLM
from rag.application.rag.index.graph.windows import (
    build_extraction_windows,
)
from rag.domain.models.content import ReadingBlock
from rag.domain.models.graph import KnowledgeRelationType
from common.utils.document import Section, SourceSpan
from rag.domain.models.structure import DocumentStructure, StructureMode
from rag.domain.repositories.mongo.published_resource_reader import GraphBuildSource


def _source(text: str, *, split: int | None = None) -> GraphBuildSource:
    spans = (
        [SourceSpan(0, len(text))]
        if split is None
        else [SourceSpan(0, split), SourceSpan(split, len(text))]
    )
    block = ReadingBlock(
        block_id="block-1",
        section_id="section-1",
        ordinal=0,
        raw_text=(text if split is None else f"{text[:split]}\n\n{text[split:]}"),
        source_spans=spans,
    )
    return GraphBuildSource(
        resource_id="resource-1",
        content_revision="revision-1",
        markdown=text,
        structure=DocumentStructure(
            mode=StructureMode.SECTIONED,
            total_length=len(text),
            sections=[
                Section(
                    section_id="section-1",
                    title="标题",
                    level=1,
                    parent_section_id=None,
                    ordinal=0,
                    section_path=["标题"],
                    own_span=SourceSpan(0, len(text)),
                    subtree_span=SourceSpan(0, len(text)),
                )
            ],
        ),
        reading_blocks=[block],
    )


def _candidate_graph(window_id: str, *, assertion: str = "affirmed") -> Neo4jGraph:
    source_id = f"{window_id}:source"
    target_id = f"{window_id}:target"
    return Neo4jGraph(
        nodes=[
            Neo4jNode(
                id=source_id,
                label="Resource",
                properties={"name": "当前资源", "resource_id": "resource-1"},
            ),
            Neo4jNode(
                id=target_id,
                label="Entity",
                properties={
                    "name": "方法甲",
                    "entity_type": "method",
                    "evidence_quote": "方法甲",
                },
            ),
        ],
        relationships=[
            Neo4jRelationship(
                start_node_id=source_id,
                end_node_id=target_id,
                type="ABOUT",
                properties={
                    "assertion": assertion,
                    "evidence_quote": "方法甲",
                },
            )
        ],
    )


def test_windows_preserve_source_mapping_and_split_long_blocks() -> None:
    text = "方法甲" + "x" * 7_000
    windows = build_extraction_windows(_source(text))

    assert len(windows) == 2
    assert len(windows[0].text) == 4_000
    assert windows[1].source_mappings[0].source_span.start_offset == 3_400


def test_validator_accepts_only_continuous_mapped_quote() -> None:
    window = build_extraction_windows(_source("方法甲用于任务乙"))[0]
    validator = KnowledgeCandidateValidator(frozenset({KnowledgeRelationType.ABOUT}))

    result = validator.validate(_candidate_graph(window.window_id), window)

    assert result.relations[0].evidence.quote == "方法甲"
    assert result.relations[0].evidence.reading_block_id == "block-1"
    assert result.relations[0].evidence.source_span == SourceSpan(0, 3)


def test_validator_discards_invalid_nodes_relations_and_non_affirmed_assertions() -> (
    None
):
    window = build_extraction_windows(_source("方法甲"))[0]
    validator = KnowledgeCandidateValidator(frozenset({KnowledgeRelationType.ABOUT}))
    graph = _candidate_graph(window.window_id, assertion="negated")
    graph.nodes.append(
        Neo4jNode(
            id=f"{window.window_id}:invalid",
            label="Unknown",
            properties={"name": "非法节点"},
        )
    )
    graph.relationships.append(
        Neo4jRelationship(
            start_node_id=f"{window.window_id}:source",
            end_node_id=f"{window.window_id}:target",
            type="UNKNOWN",
            properties={"assertion": "affirmed", "evidence_quote": "方法甲"},
        )
    )

    result = validator.validate(graph, window)

    assert len(result.nodes) == 2
    assert result.relations == []


class _QueryClient:
    model = "model"
    thinking = None


class _ArtifactStore:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.set_calls = []

    async def get_many(self, **kwargs):
        return {kwargs["artifact_keys"][0]: self.payload}

    async def set_many(self, **kwargs):
        self.set_calls.append(kwargs)


class _SourceReader:
    def __init__(self, source: GraphBuildSource) -> None:
        self.source = source

    async def get_graph_build_source(self, resource_id, content_revision):
        return self.source


@pytest.mark.asyncio
async def test_stored_candidate_artifacts_are_revalidated() -> None:
    window = build_extraction_windows(_source("方法甲"))[0]
    artifact_store = _ArtifactStore(
        _encode_candidate_graph(
            _candidate_graph(window.window_id, assertion="negated"),
            window.window_id,
        )
    )
    extractor = KnowledgeGraphExtractor(
        llm=QueryClientGraphRagLLM(client=_QueryClient()),
        generation_artifact_store=artifact_store,
        source_reader=_SourceReader(_source("方法甲")),
    )

    result = await extractor.extract(
        resource_id="resource-1",
        content_revision="revision-1",
    )

    assert result[0].relations == []
    assert artifact_store.set_calls == []

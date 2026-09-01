"""P2-A 图谱事实、插件和文本证据边界测试。"""

from collections import defaultdict
from dataclasses import replace

import pytest
from common.utils.document import SourceSpan
from pydantic import ConfigDict

from rag_v3.application.document import DocumentPreparer
from rag_v3.application.graph.graph_fact_builder import (
    GraphFactBuilder,
    _collect_llm_facts,
    _ExtractedNode,
    _GraphExtraction,
)
from rag_v3.application.publication import DocumentPublication
from rag_v3.domain.graph import (
    EntitySpec,
    GraphEdge,
    GraphNode,
    Ontology,
    RelationSpec,
    graph_edge_id,
    graph_node_id,
)
from rag_v3.domain.models import DocumentMetadata, ResourceIndexState
from rag_v3.domain.plugins import DocumentMetadataRegistry, GraphPlugin

from .conftest import (
    MemoryDocChunks,
    MemoryDocuments,
    MemoryDocumentVectors,
    MemoryIndexStates,
    chunk_for_document,
    document,
)


class PaperMetadata(DocumentMetadata):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_type: str = "paper"
    title: str
    reference_title: str


class _CitationProducer:
    def produce(self, item):
        metadata = item.metadata
        source = GraphNode(
            node_id=graph_node_id(category="paper", name=metadata.title),
            name=metadata.title,
            category="paper",
        )
        target = GraphNode(
            node_id=graph_node_id(category="paper", name=metadata.reference_title),
            name=metadata.reference_title,
            category="paper",
        )
        return (
            (source, target),
            (
                GraphEdge(
                    edge_id=graph_edge_id(
                        source_node_id=source.node_id,
                        relation_type="CITES",
                        target_node_id=target.node_id,
                    ),
                    source_node_id=source.node_id,
                    target_node_id=target.node_id,
                    relation_type="CITES",
                ),
            ),
        )


class _GraphFacts:
    def __init__(self) -> None:
        self.calls = []

    async def replace_revision(self, **kwargs) -> None:
        self.calls.append(kwargs)


def _plugin(*, enable_llm_extraction: bool = False) -> GraphPlugin:
    return GraphPlugin(
        plugin_id="paper-citation-v1",
        metadata_type=PaperMetadata,
        ontology=Ontology(
            domain="paper",
            entity_specs={"paper": EntitySpec(category="paper", description="论文")},
            relation_specs={
                "CITES": RelationSpec(
                    relation_type="CITES",
                    description="引用",
                    allowed_sources=("paper",),
                    allowed_targets=("paper",),
                )
            },
        ),
        deterministic_producer=_CitationProducer(),
        enable_llm_extraction=enable_llm_extraction,
    )


def test_metadata_registry_only_decodes_registered_types() -> None:
    registry = DocumentMetadataRegistry([_plugin()])
    metadata = PaperMetadata(title="Source", reference_title="Target")

    assert registry.decode(registry.encode(metadata)) == metadata
    with pytest.raises(ValueError, match="unregistered"):
        registry.decode({"document_type": "unknown"})


@pytest.mark.asyncio
async def test_preparer_preserves_typed_metadata_and_same_revision_cannot_change_it() -> None:
    documents = MemoryDocuments()
    chunks = MemoryDocChunks()
    states = MemoryIndexStates()
    publication = DocumentPublication(
        documents=documents,
        doc_chunks=chunks,
        document_vectors=MemoryDocumentVectors(),
        index_states=states,
    )
    preparer = DocumentPreparer(publication=publication, doc_chunks=chunks)
    metadata = PaperMetadata(title="Source", reference_title="Target")

    await preparer.prepare(
        resource_id="paper",
        document_version=1,
        markdown="# Source",
        metadata=metadata,
    )
    revision = next(iter(documents.documents))
    assert documents.documents[revision].metadata == metadata

    with pytest.raises(ValueError, match="metadata differs"):
        await documents.save_revision(
            replace(
                documents.documents[revision],
                metadata=PaperMetadata(title="Other", reference_title="Target"),
            )
        )


@pytest.mark.asyncio
async def test_deterministic_plugin_writes_facts_without_text_evidence() -> None:
    documents = MemoryDocuments()
    chunks = MemoryDocChunks()
    states = MemoryIndexStates()
    item = replace(
        document(resource_id="paper", version=1, section_id="section"),
        metadata=PaperMetadata(title="Source", reference_title="Target"),
    )
    chunk = chunk_for_document(item)
    await documents.save_revision(item)
    await chunks.save_revision([chunk])
    await states.stage_revision(item.revision, expected_applied_content_revision=None)
    await states.apply_revision(item.revision)
    facts = _GraphFacts()
    builder = GraphFactBuilder(
        documents=documents,
        doc_chunks=chunks,
        graph_facts=facts,
        index_states=states,
        plugins=(_plugin(),),
        openai_client=None,
        query_model="model",
        max_concurrency=1,
    )

    result = await builder.build(resource_id="paper")

    assert result is not None
    assert (result.node_count, result.edge_count, result.evidence_count) == (2, 1, 0)
    assert len(facts.calls) == 1
    assert facts.calls[0]["evidences"] == ()
    assert facts.calls[0]["nodes"][0].producer_id == "paper-citation-v1"


def test_llm_quote_must_be_unique_inside_target_chunk() -> None:
    item = document(
        resource_id="resource",
        version=1,
        section_id="section",
        raw_content="Alpha is a method. Context Alpha is elsewhere.",
    )
    chunk = replace(
        chunk_for_document(item),
        raw_text="Alpha is a method.",
        source_spans=(SourceSpan(0, 18),),
    )
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}
    evidences = []
    evidence_ids_by_target = defaultdict(list)
    chunk_node_ids = defaultdict(list)
    extraction = _GraphExtraction(
        nodes=(
            _ExtractedNode(
                local_id="alpha",
                name="Alpha",
                category="paper",
                quote="Alpha",
            ),
        )
    )

    _collect_llm_facts(
        document=item,
        chunk=chunk,
        extraction=extraction,
        plugin=_plugin(),
        nodes=nodes,
        edges=edges,
        evidences=evidences,
        evidence_ids_by_target=evidence_ids_by_target,
        chunk_node_ids=chunk_node_ids,
    )

    assert len(nodes) == 1
    assert evidences[0].quote_text == "Alpha"
    assert evidences[0].source_spans == (SourceSpan(0, 5),)

    duplicate = _GraphExtraction(
        nodes=(
            _ExtractedNode(
                local_id="other",
                name="Other",
                category="paper",
                quote="Alpha",
            ),
        )
    )
    duplicate_chunk = replace(
        chunk,
        source_spans=(SourceSpan(0, len(item.raw_content)),),
    )
    _collect_llm_facts(
        document=item,
        chunk=duplicate_chunk,
        extraction=duplicate,
        plugin=_plugin(),
        nodes=nodes,
        edges=edges,
        evidences=evidences,
        evidence_ids_by_target=evidence_ids_by_target,
        chunk_node_ids=chunk_node_ids,
    )
    assert len(nodes) == 1


@pytest.mark.asyncio
async def test_general_document_skips_graph_build_without_model_call() -> None:
    documents = MemoryDocuments()
    chunks = MemoryDocChunks()
    states = MemoryIndexStates()
    item = document(resource_id="general", version=1, section_id="section")
    await documents.save_revision(item)
    await chunks.save_revision([chunk_for_document(item)])
    await states.stage_revision(item.revision, expected_applied_content_revision=None)
    await states.apply_revision(item.revision)
    facts = _GraphFacts()
    builder = GraphFactBuilder(
        documents=documents,
        doc_chunks=chunks,
        graph_facts=facts,
        index_states=states,
        plugins=(_plugin(),),
        openai_client=None,
        query_model="model",
        max_concurrency=1,
    )

    result = await builder.build(resource_id="general")

    assert result is not None
    assert result.node_count == result.edge_count == result.evidence_count == 0
    assert facts.calls == []


@pytest.mark.asyncio
async def test_active_switch_during_build_prevents_old_graph_write() -> None:
    documents = MemoryDocuments()
    states = MemoryIndexStates()
    item = replace(
        document(resource_id="paper", version=1, section_id="section"),
        metadata=PaperMetadata(title="Source", reference_title="Target"),
    )
    await documents.save_revision(item)
    await states.stage_revision(item.revision, expected_applied_content_revision=None)
    await states.apply_revision(item.revision)

    class _ChangingChunks(MemoryDocChunks):
        async def get_revision_chunks(self, *, resource_id, content_revision):
            result = await super().get_revision_chunks(
                resource_id=resource_id,
                content_revision=content_revision,
            )
            states.states[resource_id] = ResourceIndexState(
                resource_id=resource_id,
                applied_content_revision="newer-revision",
            )
            return result

    chunks = _ChangingChunks()
    await chunks.save_revision([chunk_for_document(item)])
    facts = _GraphFacts()
    builder = GraphFactBuilder(
        documents=documents,
        doc_chunks=chunks,
        graph_facts=facts,
        index_states=states,
        plugins=(_plugin(),),
        openai_client=None,
        query_model="model",
        max_concurrency=1,
    )

    with pytest.raises(RuntimeError, match="active revision changed"):
        await builder.build(resource_id="paper")
    assert facts.calls == []

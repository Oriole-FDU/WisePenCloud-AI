from dataclasses import asdict, fields, is_dataclass, replace

import pytest
from common.utils.markdown import Anchor, Section, SourceSpan

from rag import ingestion
from rag.core.schema import Chunk, Document, DocumentStructure, GraphEdge, GraphNode


def test_content_models_are_owned_by_schema():
    assert all(
        model.__module__ == "rag.core.schema.content"
        for model in (Document, DocumentStructure, Chunk)
    )
    assert not hasattr(ingestion, "Chunk")


def test_document_keeps_common_structure_and_content_facts():
    markdown = "# 标题\n正文"
    span = SourceSpan(0, len(markdown))
    section = Section(
        section_id="common-section",
        title="标题",
        level=1,
        parent_section_id=None,
        ordinal=0,
        section_path=["标题"],
        own_span=span,
        subtree_span=span,
    )
    anchor = Anchor(label="table:1", source_span=span)
    structure = DocumentStructure(
        total_length=len(markdown), sections=[section], anchors=[anchor]
    )
    metadata = {"contextual_token_threshold": 100, "custom": {"enabled": True}}
    document = Document(
        resource_id="resource",
        document_version=7,
        content_revision="revision",
        text=markdown,
        structure=structure,
        metadata=metadata,
    )

    assert document.resource_id == "resource"
    assert document.document_version == 7
    assert document.content_revision == "revision"
    assert document.text == markdown
    assert document.structure is structure
    assert structure.total_length == len(markdown)
    assert structure.sections[0] is section
    assert structure.anchors[0] is anchor
    assert document.metadata is metadata
    assert {item.name for item in fields(Document)} == {
        "resource_id",
        "document_version",
        "content_revision",
        "text",
        "structure",
        "metadata",
        "type",
    }
    assert {item.name for item in fields(DocumentStructure)} == {
        "total_length",
        "sections",
        "anchors",
    }


@pytest.mark.parametrize(
    ("name", "ontology_name", "category", "expected"),
    [
        ("  Alice \t SMITH\n", "people", "Person", "people:Person:alice smith"),
        ("  Alice \t SMITH\n", None, "Person", "Person:alice smith"),
        ("Straße\u2003 KNOWLEDGE", "词汇", "Concept", "词汇:Concept:strasse knowledge"),
        ("A-B / C", None, "Concept", "Concept:a-b / c"),
        ("ＦＯＯ", None, "Concept", "Concept:ｆｏｏ"),
        ("Name", None, None, ":name"),
        ("Name", "ontology", None, "ontology::name"),
    ],
)
def test_graph_node_id_normalizes_only_name(name, ontology_name, category, expected):
    node = GraphNode(
        name=name, ontology_name=ontology_name, category=category, source_id="chunk-1"
    )

    assert is_dataclass(node) and not hasattr(node, "__dict__")
    assert node.id == expected
    assert node.name == name
    assert node.source_id == "chunk-1"
    assert set(asdict(node)) == {
        "name",
        "ontology_name",
        "category",
        "description",
        "relations",
        "source_id",
    }
    # 来源不同不改变实体的语义身份，provenance 仍留在事实中。
    assert replace(node, source_id="chunk-2").id == node.id


def test_graph_edge_id_preserves_direction_relation_and_provenance():
    source = GraphNode(name="Alice", category="Person", source_id="chunk-1")
    target = GraphNode(name="Bob", category="Person", source_id="chunk-2")
    edge = GraphEdge(
        source_id=source.id,
        target_id=target.id,
        relation_type="Knows",
        description="同事",
        keywords=["工作"],
        source_ids=["chunk-1", "chunk-2"],
        metadata={"custom": {"weight": 2}},
    )

    assert is_dataclass(edge) and not hasattr(edge, "__dict__")
    assert edge.id == "Person:alice::Knows::Person:bob"
    assert edge.source_ids == ["chunk-1", "chunk-2"]
    assert edge.metadata == {"custom": {"weight": 2}}
    assert set(asdict(edge)) == {
        "source_id",
        "target_id",
        "relation_type",
        "description",
        "keywords",
        "source_ids",
        "metadata",
    }
    reversed_edge = replace(edge, source_id=target.id, target_id=source.id)
    assert reversed_edge.id != edge.id


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (None, " 原始正文\n"),
        ("", " 原始正文\n"),
        (" \t\n", " 原始正文\n"),
        ("  文档上下文\n ", "文档上下文\n\n 原始正文\n"),
    ],
)
def test_chunk_retrieval_text_uses_only_contextual_context(context, expected):
    metadata = {
        "contextual_context": context,
        "retrieval_context": "忽略旧键",
        "entities": [{"name": "任意图事实"}],
        "custom": object(),
    }
    chunk = Chunk(
        chunk_id="chunk",
        resource_id="resource",
        content_revision="revision",
        chunk_index=2,
        section_id=None,
        section_path=[],
        raw_text=" 原始正文\n",
        content_token_count=3,
        anchor_labels=[],
        metadata=metadata,
    )

    assert chunk.retrieval_text == expected
    assert chunk.raw_text == " 原始正文\n"
    assert chunk.metadata is metadata
    assert (chunk.chunk_id, chunk.chunk_index, chunk.content_revision) == (
        "chunk",
        2,
        "revision",
    )
    assert not hasattr(chunk, "__dict__")


def test_model_collection_defaults_are_independent():
    first = DocumentStructure(total_length=0)
    second = DocumentStructure(total_length=0)
    assert first.sections is not second.sections
    assert first.anchors is not second.anchors

    first_document = Document("resource", 1, "revision", "", first)
    second_document = Document("resource", 1, "revision", "", second)
    first_document.metadata["custom"] = True
    assert second_document.metadata == {}

    first_chunk = Chunk("chunk", "resource", "revision", 0, None, [], "text", 1, [])
    second_chunk = Chunk("chunk", "resource", "revision", 0, None, [], "text", 1, [])
    first_chunk.metadata["custom"] = True
    assert second_chunk.metadata == {}
    assert second_chunk.retrieval_text == second_chunk.raw_text

    first_edge = GraphEdge(source_id="a", target_id="b", relation_type="knows")
    second_edge = GraphEdge(source_id="a", target_id="b", relation_type="knows")
    first_edge.source_ids.append("chunk")
    first_edge.metadata["custom"] = True
    assert second_edge.source_ids == []
    assert second_edge.metadata == {}


@pytest.mark.parametrize("path", [[], ["标题"], ["上层", "A>B"]])
@pytest.mark.parametrize("context", ["", "生成上下文"])
def test_retrieval_prefixes_do_not_change_body_facts(chunk, path, context):
    chunk.section_path = path
    chunk.metadata = {"contextual_context": context}
    original = (chunk.raw_text, chunk.content_token_count, chunk.chunk_id)
    prefixes = [part for part in (">".join(path), context) if part]
    assert chunk.retrieval_text == "\n\n".join([*prefixes, chunk.raw_text])
    assert (chunk.raw_text, chunk.content_token_count, chunk.chunk_id) == original

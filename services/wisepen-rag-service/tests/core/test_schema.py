from dataclasses import replace

import pytest

from rag.core.schema import GraphEdge, GraphNode


def test_graph_node_identity_is_normalized_and_source_independent():
    node = GraphNode(
        name="  Alice \t SMITH\n",
        ontology_name="people",
        category="Person",
        source_id="chunk-1",
    )

    assert node.id == "people:Person:alice smith"
    assert replace(node, source_id="chunk-2").id == node.id


def test_graph_edge_identity_keeps_direction_and_provenance():
    source = GraphNode(name="Alice", category="Person", source_id="chunk-1")
    target = GraphNode(name="Bob", category="Person", source_id="chunk-2")
    edge = GraphEdge(
        source_id=source.id,
        target_id=target.id,
        relation_type="Knows",
        source_ids=["chunk-1", "chunk-2"],
    )

    assert edge.id == "Person:alice::Knows::Person:bob"
    assert edge.source_ids == ["chunk-1", "chunk-2"]
    assert replace(edge, source_id=target.id, target_id=source.id).id != edge.id


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ("", " 原始正文\n"),
        ("  文档上下文\n ", "文档上下文\n\n 原始正文\n"),
    ],
)
def test_chunk_retrieval_text_uses_context_without_changing_raw_text(
    chunk, context, expected
):
    chunk.raw_text = " 原始正文\n"
    chunk.section_path = []
    chunk.metadata = {
        "contextual_context": context,
        "retrieval_context": "忽略旧键",
        "entities": [{"name": "不加入向量文本的图事实"}],
    }
    raw_text = chunk.raw_text

    assert chunk.retrieval_text == expected
    assert chunk.raw_text == raw_text


@pytest.mark.parametrize("path", [[], ["标题"], ["上层", "子标题"]])
@pytest.mark.parametrize("context", ["", "生成上下文"])
def test_retrieval_prefixes_preserve_chunk_content(chunk, path, context):
    chunk.section_path = path
    chunk.metadata = {"contextual_context": context}
    original = (chunk.raw_text, chunk.content_token_count, chunk.chunk_id)
    prefixes = [part for part in (">".join(path), context) if part]

    assert chunk.retrieval_text == "\n\n".join([*prefixes, chunk.raw_text])
    assert (chunk.raw_text, chunk.content_token_count, chunk.chunk_id) == original

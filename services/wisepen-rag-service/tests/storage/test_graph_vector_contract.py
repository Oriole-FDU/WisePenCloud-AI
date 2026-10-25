"""图向量的来源身份与索引语义，和后端 UUID、业务 metadata 解耦。"""

from dataclasses import asdict, replace

import pytest

from rag.storage.graph import (
    GraphVectorKind,
    entity_vector_text,
    graph_vector_record_id,
    relationship_vector_text,
)


def test_ontology_checks_node_category_and_relation_direction(graph_ontology, chunk):
    node = graph_ontology.create_node(
        name="张三", category="Person", source_id=chunk.chunk_id
    )
    target = graph_ontology.create_node(
        name="甲公司", category="Company", source_id=chunk.chunk_id
    )
    edge = graph_ontology.create_edge(
        nodes={node.id: node, target.id: target},
        source_id=node.id,
        target_id=target.id,
        relation_type="works_at",
        chunk_id=chunk.chunk_id,
    )
    assert node.ontology_name == "work" and edge.source_ids == [chunk.chunk_id]
    with pytest.raises(ValueError):
        graph_ontology.create_node(
            name="张三", category="unknown", source_id=chunk.chunk_id
        )
    with pytest.raises(ValueError):
        graph_ontology.create_edge(
            nodes={node.id: node, target.id: target},
            source_id=target.id,
            target_id=node.id,
            relation_type="works_at",
            chunk_id=chunk.chunk_id,
        )


def test_complete_ontology_semantics_and_optional_descriptions(
        graph_ontology, chunk, make_graph_facts
):
    nodes, edges = make_graph_facts(chunk)
    entity = entity_vector_text(nodes[0], graph_ontology)
    relation = relationship_vector_text(
        edges[0], {node.id: node for node in nodes}, graph_ontology
    )
    assert (
            entity
            == "ontology: work\nontology_description: 工作关系\ncategory: Person\ncategory_description: 人员\nname: 张三\ndescription: 来源描述"
    )
    assert all(
        text in relation
        for text in [
            "工作关系",
            "人员就职于公司",
            "source: 张三",
            "target: 甲公司",
            "source_category_description: 人员",
            "target_category_description: 公司",
            "keywords: 任职",
        ]
    )
    assert entity_vector_text(
        replace(nodes[0], description=None), graph_ontology
    ).endswith("description: ")
    assert relationship_vector_text(
        replace(edges[0], keywords=None),
        {node.id: node for node in nodes},
        graph_ontology,
    ).endswith("keywords: ")
    assert (
            entity_vector_text(
                nodes[0], replace(graph_ontology, description="另一本体说明")
            )
            != entity
    )
    assert (
            entity_vector_text(
                nodes[0],
                replace(
                    graph_ontology,
                    entity_specs={
                        **graph_ontology.entity_specs,
                        "Person": replace(
                            graph_ontology.entity_specs["Person"],
                            description="新的类别含义",
                        ),
                    },
                ),
            )
            != entity
    )


def test_record_identity_includes_kind_source_and_fact_without_delimiter_alias():
    identifier = graph_vector_record_id(GraphVectorKind.ENTITY, "a", "b")
    assert identifier.startswith("gvec_") and len(identifier) == 69
    assert identifier == graph_vector_record_id(GraphVectorKind.ENTITY, "a", "b")
    assert (
            len(
                {
                    identifier,
                    graph_vector_record_id(GraphVectorKind.RELATIONSHIP, "a", "b"),
                    graph_vector_record_id(GraphVectorKind.ENTITY, "other", "b"),
                    graph_vector_record_id(GraphVectorKind.ENTITY, "a", "other"),
                }
            )
            == 4
    )
    assert graph_vector_record_id(
        GraphVectorKind.ENTITY, "a::b", "c"
    ) != graph_vector_record_id(GraphVectorKind.ENTITY, "a", "b::c")


def test_strict_cache_and_ordinary_projection_share_fact_rules(
        graph_ontology, chunk, make_graph_facts
):
    nodes, edges = make_graph_facts(chunk)
    chunk.metadata["entities"].append(asdict(replace(nodes[0], description="不覆盖")))
    chunk.metadata["relationships"].append(
        asdict(replace(edges[0], description="不覆盖"))
    )
    assert graph_ontology.parse_chunk_facts(chunk) == (
        {node.id: node for node in nodes},
        {edge.id: edge for edge in edges},
    )
    with pytest.raises(ValueError, match="duplicate"):
        graph_ontology.parse_chunk_facts(chunk, strict=True)
    chunk.metadata["entities"] = [
        asdict(replace(nodes[0], category="unknown")),
        asdict(nodes[1]),
    ]
    chunk.metadata["relationships"] = []
    assert graph_ontology.parse_chunk_facts(chunk)[0] == {nodes[1].id: nodes[1]}
    with pytest.raises(ValueError, match="unknown"):
        graph_ontology.parse_chunk_facts(chunk, strict=True)

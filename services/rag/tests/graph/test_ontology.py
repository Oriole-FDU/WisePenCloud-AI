"""本体输入与方向约束不依赖插件或存储。"""

import json
from dataclasses import asdict, is_dataclass, replace

import pytest
import yaml

from rag.core.schema import EntitySpec, GraphEdge, GraphNode, Ontology, RelationSpec
from rag.ingestion.transformers.graph import GraphExtraction


def test_domain_and_unrestricted_endpoint_categories(graph_ontology):
    source = GraphNode(
        name="张三", category="Person", ontology_name="work", source_id="chunk"
    )
    target = GraphNode(
        name="甲公司", category="Company", ontology_name="work", source_id="chunk"
    )
    nodes = {node.id: node for node in [source, target]}
    edge = GraphEdge(source_id=source.id, target_id=target.id, relation_type="works_at")
    graph_ontology.validate_node(source)
    graph_ontology.validate_edge(edge, nodes)
    with pytest.raises(ValueError, match="source category"):
        graph_ontology.validate_edge(
            replace(edge, source_id=target.id, target_id=source.id),
            nodes,
        )
    graph_ontology.relation_specs["works_at"] = replace(
        graph_ontology.relation_specs["works_at"],
        allowed_sources=[],
        allowed_targets=[],
    )
    graph_ontology.validate_edge(
        replace(edge, source_id=target.id, target_id=source.id), nodes
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"domain": " "},
        {"entity_specs": {"Wrong": {"category": "Person", "description": "人员"}}},
        {
            "relation_specs": {
                "wrong": {"relation_type": "works_at", "description": "就职"}
            }
        },
        {
            "relation_specs": {
                "works_at": {
                    "relation_type": "works_at",
                    "description": "就职",
                    "allowed_targets": ["Unknown"],
                }
            }
        },
        {"version": "invented"},
    ],
)
def test_invalid_ontology_file_contract(graph_ontology, changes, tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({**asdict(graph_ontology), **changes}), encoding="utf-8")
    with pytest.raises(ValueError):
        Ontology.from_file(path)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"name": " "}, "blank"),
        ({"category": None}, "category"),
        ({"category": "Unknown"}, "category"),
        ({"ontology_name": "other"}, "ontology"),
    ],
)
def test_node_business_constraints(graph_ontology, changes, message):
    node = GraphNode(
        name="张三", category="Person", ontology_name="work", source_id="chunk"
    )
    with pytest.raises(ValueError, match=message):
        graph_ontology.validate_node(replace(node, **changes))


def test_instructor_schema_fields_have_descriptions():
    schema = GraphExtraction.model_json_schema()
    for definition in [schema, *schema.get("$defs", {}).values()]:
        assert all(
            value.get("description")
            for value in definition.get("properties", {}).values()
        )


@pytest.mark.parametrize("suffix", [".json", ".yaml", ".yml", ".YAML"])
def test_load_ontology_file_as_lightweight_dataclasses(
    graph_ontology, tmp_path, suffix
):
    path = tmp_path / f"ontology{suffix}"
    data = asdict(graph_ontology)
    text = (
        json.dumps(data, ensure_ascii=False)
        if suffix == ".json"
        else yaml.safe_dump(data, allow_unicode=True)
    )
    path.write_text(text, encoding="utf-8-sig")
    loaded = Ontology.from_file(str(path))
    assert loaded == graph_ontology
    assert is_dataclass(loaded)
    assert isinstance(loaded.entity_specs["Person"], EntitySpec)
    assert isinstance(loaded.relation_specs["works_at"], RelationSpec)


@pytest.mark.parametrize(
    "filename, text",
    [
        ("unsupported.toml", ""),
        ("bad.json", "{"),
        ("bad.yaml", "domain: [unfinished"),
        ("bad.yml", "!!python/object:os.system {}"),
        ("empty.yaml", ""),
        ("list.json", "[]"),
        (
            "nested.json",
            '{"domain": "work", "entity_specs": {"Person": {"category": "Person", "description": "人员", "unknown": true}}}',
        ),
        ("types.json", '{"domain": 1}'),
    ],
)
def test_invalid_file_formats_and_shapes_are_rejected(tmp_path, filename, text):
    path = tmp_path / filename
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        Ontology.from_file(path)


def test_missing_file_raises_io_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        Ontology.from_file(tmp_path / "missing.json")

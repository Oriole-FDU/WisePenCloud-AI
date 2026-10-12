"""图事实与本体的轻量模型；配置文件和缓存解析留在外部数据边界。"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import TypeAdapter


@dataclass(slots=True)
class GraphNode:
    """实体的语义身份与单次抽取来源，不携带资源可见性投影。"""

    name: str  # 实体名称；身份仅规范化名称的大小写与空白。
    source_id: str  # 程序绑定的来源 Chunk ID，不是图节点 ID。
    ontology_name: str | None = None  # 本体 domain，参与实体身份。
    category: str | None = None  # 实体类别；本体抽取必须提供合法类别。
    description: str | None = None  # 正文支持的描述，不影响身份。

    @property
    def id(self) -> str:
        # 只规范化实体名；缺失类别保留空槽，ontology/category 保持原值。
        normalized_name = " ".join(self.name.strip().casefold().split())
        category = self.category or ""
        if self.ontology_name:
            return f"{self.ontology_name}:{category}:{normalized_name}"
        return f"{category}:{normalized_name}"


@dataclass(slots=True)
class GraphEdge:
    """有向关系及其来源；端点 ID 与 provenance 分别表达拓扑和来源。"""

    source_id: str  # 起点 GraphNode.id，不是来源 Chunk ID。
    target_id: str  # 终点 GraphNode.id；关系保留方向。
    relation_type: str  # 本体关系谓词，参与身份。
    description: str | None = None  # 正文支持的描述，不影响身份。
    keywords: list[str] | None = None  # 关系语义关键词，可为空。
    source_ids: list[str] = field(default_factory=list)  # 来源 Chunk ID 列表。
    metadata: dict[str, object] = field(default_factory=dict)  # 非 ACL/发布状态。

    @property
    def id(self) -> str:
        return f"{self.source_id}::{self.relation_type}::{self.target_id}"


@dataclass(frozen=True, slots=True)
class EntitySpec:
    """本体内一个实体类别，不携带旧运行时的节点层级类型。"""

    category: str  # 类别标识，与 entity_specs 的键一致。
    description: str  # 该类别的业务含义及抽取依据。


@dataclass(frozen=True, slots=True)
class RelationSpec:
    """一个有向谓词及允许的起点、终点类别。"""

    relation_type: str  # 谓词标识，与 relation_specs 的键一致。
    description: str  # 关系含义与方向。
    allowed_sources: list[str] = field(default_factory=list)  # 空列表不限制起点。
    allowed_targets: list[str] = field(default_factory=list)  # 空列表不限制终点。


@dataclass(frozen=True, slots=True)
class Ontology:
    """抽取绑定的本体，可直接构造或从 JSON/YAML 文件加载。"""

    domain: str  # 映射 GraphNode.ontology_name，参与实体身份。
    description: str = ""  # 本体整体业务说明。
    entity_specs: dict[str, EntitySpec] = field(default_factory=dict)
    relation_specs: dict[str, RelationSpec] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str | Path) -> "Ontology":
        """解析本地配置并校验声明；内部直接构造仍使用普通 dataclass。

        支持 .json、.yaml、.yml 和 UTF-8 BOM；YAML 使用 safe_load，
        TypeAdapter 仅在此文件边界把嵌套字典解析成现有模型，不创建镜像 DTO。
        """
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix not in (".json", ".yaml", ".yml"):
            raise ValueError("ontology file must be JSON or YAML")

        text = path.read_text(encoding="utf-8-sig")
        try:
            data = json.loads(text) if suffix == ".json" else yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ValueError("invalid ontology YAML") from exc

        ontology = TypeAdapter(cls).validate_python(data, extra="forbid")

        # 文件声明在此拒绝不一致规则，防止 prompt 使用值、硬校验使用键。
        if not ontology.domain.strip():
            raise ValueError("ontology domain must not be blank")
        for category, spec in ontology.entity_specs.items():
            if not category.strip() or category != spec.category:
                raise ValueError("entity spec key must match its nonblank category")
        for relation_type, spec in ontology.relation_specs.items():
            if not relation_type.strip() or relation_type != spec.relation_type:
                raise ValueError("relation spec key must match its nonblank type")
            if any(
                category not in ontology.entity_specs
                for category in [*spec.allowed_sources, *spec.allowed_targets]
            ):
                raise ValueError("relation endpoint category is not declared")
        return ontology

    def validate_node(self, node: GraphNode) -> None:
        if node.ontology_name != self.domain:
            raise ValueError("entity ontology does not match")
        if not node.name.strip():
            raise ValueError("entity name must not be blank")
        if node.category not in self.entity_specs:
            raise ValueError(f"unknown entity category: {node.category}")

    def validate_edge(self, edge: GraphEdge, nodes: Mapping[str, GraphNode]) -> None:
        spec = self.relation_specs.get(edge.relation_type)
        if spec is None:
            raise ValueError(f"unknown relation type: {edge.relation_type}")
        source = nodes.get(edge.source_id)
        target = nodes.get(edge.target_id)
        if source is None or target is None:
            raise ValueError("relation endpoint is missing")
        if spec.allowed_sources and source.category not in spec.allowed_sources:
            raise ValueError("relation source category is not allowed")
        if spec.allowed_targets and target.category not in spec.allowed_targets:
            raise ValueError("relation target category is not allowed")

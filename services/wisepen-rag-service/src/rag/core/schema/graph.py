"""图事实与本体的轻量模型；配置文件和缓存解析留在外部数据边界。"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import yaml
from common.logger import warning
from pydantic import TypeAdapter

from .content import Chunk


class GraphDirection(StrEnum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"
    BOTH = "both"


@dataclass(slots=True)
class GraphRelationOption:
    """类别允许的谓词与方向提示，不表示当前可见事实存在该关系。"""

    relation_type: str
    direction: GraphDirection


@dataclass(slots=True)
class GraphNode:
    """实体的语义身份与单次抽取来源，不携带资源可见性投影。"""

    name: str  # 实体名称；身份仅规范化名称的大小写与空白。
    source_id: str  # 程序绑定的来源 Chunk ID，不是图节点 ID。
    ontology_name: str | None = None  # 本体 domain，参与实体身份。
    category: str | None = None  # 实体类别；本体抽取必须提供合法类别。
    description: str | None = None  # 正文支持的描述，不影响身份。
    relations: list[GraphRelationOption] = field(
        default_factory=list
    )  # 写入时的类别关系提示。

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
        # 端点 + 谓词组成关系身份
        return f"{self.source_id}::{self.relation_type}::{self.target_id}"


_nodes_adapter = TypeAdapter(list[GraphNode])
_edges_adapter = TypeAdapter(list[GraphEdge])


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

        # utf-8-sig 兼容 BOM；JSON/YAML 分别走对应解析器
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
            # 关系两端的类别必须在本体中声明
            if any(
                category not in ontology.entity_specs
                for category in [*spec.allowed_sources, *spec.allowed_targets]
            ):
                raise ValueError("relation endpoint category is not declared")
        return ontology

    def validate_node(self, node: GraphNode) -> None:
        """校验实体属于本域、名称非空、类别已声明。"""
        if node.ontology_name != self.domain:
            raise ValueError("entity ontology does not match")
        if not node.name.strip():
            raise ValueError("entity name must not be blank")
        if node.category not in self.entity_specs:
            raise ValueError(f"unknown entity category: {node.category}")

    def validate_edge(self, edge: GraphEdge, nodes: Mapping[str, GraphNode]) -> None:
        """校验谓词已声明，且端点存在、类别符合允许集合。"""
        spec = self.relation_specs.get(edge.relation_type)
        if spec is None:
            raise ValueError(f"unknown relation type: {edge.relation_type}")

        source = nodes.get(edge.source_id)
        target = nodes.get(edge.target_id)
        if source is None or target is None:
            raise ValueError("relation endpoint is missing")

        # 允许集合为空表示不限制该端
        if spec.allowed_sources and source.category not in spec.allowed_sources:
            raise ValueError("relation source category is not allowed")
        if spec.allowed_targets and target.category not in spec.allowed_targets:
            raise ValueError("relation target category is not allowed")

    def create_node(
        self,
        *,
        name: str,
        category: str,
        source_id: str,
        description: str | None = None,
    ) -> GraphNode:
        """在本体内构造实体，namespace 由本体绑定，不由模型指定。"""
        node = GraphNode(name, source_id, self.domain, category, description)
        self.validate_node(node)
        for spec in self.relation_specs.values():
            outgoing = not spec.allowed_sources or category in spec.allowed_sources
            incoming = not spec.allowed_targets or category in spec.allowed_targets
            if outgoing or incoming:
                direction = (
                    GraphDirection.BOTH
                    if outgoing and incoming
                    else GraphDirection.OUTGOING
                    if outgoing
                    else GraphDirection.INCOMING
                )
                node.relations.append(
                    GraphRelationOption(spec.relation_type, direction)
                )
        return node

    def create_edge(
        self,
        *,
        nodes: Mapping[str, GraphNode],
        source_id: str,
        target_id: str,
        relation_type: str,
        chunk_id: str,
        description: str | None = None,
        keywords: list[str] | None = None,
    ) -> GraphEdge:
        """连接当前已接受实体，绑定单一 Chunk 来源，不自动补建端点。"""
        edge = GraphEdge(
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            description=description,
            keywords=keywords,
            source_ids=[chunk_id],
        )
        self.validate_edge(edge, nodes)
        return edge

    def parse_chunk_facts(
        self, chunk: Chunk, *, strict: bool = False
    ) -> tuple[dict[str, GraphNode], dict[str, GraphEdge]]:
        """写入与成功缓存按当前本体约束读取；普通查询使用 parse_graph_facts。"""
        return parse_graph_facts(chunk, ontology=self, strict=strict)


def parse_graph_facts(
    chunk: Chunk, *, ontology: Ontology | None = None, strict: bool = False
) -> tuple[dict[str, GraphNode], dict[str, GraphEdge]]:
    """解析来源事实；Ontology 仅由写入/缓存调用方提供，读取不反向套用本体约束。

    strict=True 用于成功缓存：非法项和重复 ID 均拒绝，不能丢弃后当作
    已完整成功。普通读取保留首次合法事实，不修改输入，不决定授权。
    """
    # 外部 metadata 一次解析结构；不手写重复的列表/字典/字段类型检查。
    entities = _nodes_adapter.validate_python(
        chunk.metadata.get("entities"), extra="forbid"
    )
    relationships = _edges_adapter.validate_python(
        chunk.metadata.get("relationships"), extra="forbid"
    )

    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}

    # 实体：结构错误报错；本体非法项按 strict 决定丢弃或抛错
    for node in entities:
        if node.source_id != chunk.chunk_id:
            raise ValueError("graph entity source does not match its chunk")
        if ontology is not None:
            try:
                ontology.validate_node(node)
            except ValueError as exc:
                if strict:
                    raise
                warning(
                    "graph_entity_rejected", chunk_id=chunk.chunk_id, error=str(exc)
                )
                continue
        if strict and node.id in nodes:
            raise ValueError("duplicate cached entity")
        nodes.setdefault(node.id, node)  # 同 ID 去重，保留首个

    # 关系：同上，端点必须已在 nodes 中
    for edge in relationships:
        if edge.source_ids != [chunk.chunk_id]:
            raise ValueError("graph relationship sources do not match its chunk")
        if ontology is not None:
            try:
                ontology.validate_edge(edge, nodes)
            except ValueError as exc:
                if strict:
                    raise
                warning(
                    "graph_relationship_rejected",
                    chunk_id=chunk.chunk_id,
                    error=str(exc),
                )
                continue
        elif edge.source_id not in nodes or edge.target_id not in nodes:
            warning(
                "graph_relationship_rejected",
                chunk_id=chunk.chunk_id,
                error="relation endpoint is missing",
            )
            continue
        if strict and edge.id in edges:
            raise ValueError("duplicate cached relationship")
        edges.setdefault(edge.id, edge)  # 同 ID 去重，保留首个

    return nodes, edges

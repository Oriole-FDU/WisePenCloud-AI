"""图谱逻辑事实、来源投影和 Ontology 约束。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Literal

from common.utils.document import SourceSpan
from pydantic import BaseModel, ConfigDict, Field, model_validator


class GraphNodeKind(StrEnum):
    """节点在图中的资源语义。"""

    ENTITY = "entity"
    RESOURCE = "resource"
    EXTERNAL_RESOURCE = "external_resource"


class GraphSearchLevel(StrEnum):
    """图谱入口选择的召回路径。"""

    LOW = "low"
    HIGH = "high"
    HYBRID = "hybrid"


class TraversalDirection(StrEnum):
    """从 seed 节点扩展关系的方向。"""

    IN = "in"
    OUT = "out"
    BOTH = "both"


class GraphFilterOperator(StrEnum):
    """插件可编译到图谱来源投影的最小过滤运算集合。"""

    EQ = "eq"
    GTE = "gte"
    LTE = "lte"


class GraphNode(BaseModel):
    """与资源来源无关的逻辑实体。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    name: str
    node_type: GraphNodeKind = GraphNodeKind.ENTITY
    category: str
    description: str = ""
    aliases: tuple[str, ...] = ()
    extra_meta: dict[str, object] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """与资源来源无关的逻辑关系。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    edge_id: str
    source_node_id: str
    target_node_id: str
    relation_type: str
    description: str = ""
    keywords: tuple[str, ...] = ()
    extra_meta: dict[str, object] = Field(default_factory=dict)


class TextGraphEvidence(BaseModel):
    """仅 LLM 图元必须持有的原文证据。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    target_type: Literal["node", "edge"]
    target_id: str
    resource_id: str
    content_revision: str
    section_id: str | None = None
    chunk_id: str
    source_spans: tuple[SourceSpan, ...]
    quote_text: str

    @model_validator(mode="after")
    def _require_spans(self) -> TextGraphEvidence:
        if not self.source_spans:
            raise ValueError("TextGraphEvidence requires source_spans")
        if not self.quote_text:
            raise ValueError("TextGraphEvidence requires quote_text")
        return self


class GraphNodeProjection(BaseModel):
    """一个逻辑节点在某个 revision 的 LLM 或确定性来源投影。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node: GraphNode
    resource_id: str
    content_revision: str
    evidence_ids: tuple[str, ...] = ()
    producer_id: str | None = None
    # 只保存插件明确声明、且图谱查询实际需要下推的 metadata 标量值。
    filter_values: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_source(self) -> GraphNodeProjection:
        if bool(self.evidence_ids) == bool(self.producer_id):
            raise ValueError("graph projection requires exactly one source path")
        return self


class GraphEdgeProjection(BaseModel):
    """一个逻辑关系在某个 revision 的 LLM 或确定性来源投影。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    edge: GraphEdge
    resource_id: str
    content_revision: str
    evidence_ids: tuple[str, ...] = ()
    producer_id: str | None = None
    filter_values: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_source(self) -> GraphEdgeProjection:
        if bool(self.evidence_ids) == bool(self.producer_id):
            raise ValueError("graph projection requires exactly one source path")
        return self


class EntitySpec(BaseModel):
    """一个插件允许的实体类别。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str
    description: str
    default_node_type: GraphNodeKind = GraphNodeKind.ENTITY


class RelationSpec(BaseModel):
    """一个插件允许的关系谓词与端点类别。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relation_type: str
    description: str
    allowed_sources: tuple[str, ...] = ()
    allowed_targets: tuple[str, ...] = ()


class Ontology(BaseModel):
    """垂类图谱的实体、关系及端点约束。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: str
    description: str = ""
    entity_specs: dict[str, EntitySpec] = Field(default_factory=dict)
    relation_specs: dict[str, RelationSpec] = Field(default_factory=dict)

    def validate_node(self, node: GraphNode) -> None:
        if node.category not in self.entity_specs:
            raise ValueError(f"unknown entity category: {node.category}")

    def validate_edge(self, edge: GraphEdge, nodes: Mapping[str, GraphNode]) -> None:
        spec = self.relation_specs.get(edge.relation_type)
        if spec is None:
            raise ValueError(f"unknown relation type: {edge.relation_type}")
        source = nodes.get(edge.source_node_id)
        target = nodes.get(edge.target_node_id)
        if source is None or target is None:
            raise ValueError("relation endpoint is missing")
        if spec.allowed_sources and source.category not in spec.allowed_sources:
            raise ValueError("relation source category is not allowed")
        if spec.allowed_targets and target.category not in spec.allowed_targets:
            raise ValueError("relation target category is not allowed")


class GraphRevisionFacts(BaseModel):
    """Mongo 中同一资源 revision 的完整图谱事实，用于重建外部投影。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    nodes: tuple[GraphNodeProjection, ...] = ()
    edges: tuple[GraphEdgeProjection, ...] = ()
    evidences: tuple[TextGraphEvidence, ...] = ()


class GraphFilterCondition(BaseModel):
    """已由垂类插件校验的来源投影 metadata 过滤条件。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    operator: GraphFilterOperator
    value: str | int | float | bool

    @model_validator(mode="after")
    def _require_property_safe_field(self) -> GraphFilterCondition:
        """过滤字段会同时编译为 Qdrant 路径和 Neo4j 属性名。"""
        if not self.field.isidentifier() or self.field.startswith("_"):
            raise ValueError("graph filter field must be a public identifier")
        return self


@dataclass(frozen=True, slots=True)
class GraphVectorCandidate:
    """Qdrant 图谱初检候选；不携带缓存正文或原始相似度分数。"""

    projection_id: str
    target_type: Literal["node", "edge"]
    target_id: str
    resource_id: str
    content_revision: str
    rank: int
    branch: str


@dataclass(frozen=True, slots=True)
class GraphSourceProjection:
    """Neo4j 返回的一个可归属资源 revision 的图元来源。"""

    projection_id: str
    target_type: Literal["node", "edge"]
    target_id: str
    resource_id: str
    content_revision: str
    evidence_ids: tuple[str, ...]
    producer_id: str | None
    node: GraphNode | None = None
    edge: GraphEdge | None = None
    source_node_name: str = ""
    target_node_name: str = ""
    graph_rank: int = 0
    hop_count: int = 0


@dataclass(frozen=True, slots=True)
class GraphSearchRequest:
    """图谱检索输入；metadata_filter 只能是已注册插件的强类型模型。"""

    query: str
    level: GraphSearchLevel = GraphSearchLevel.HYBRID
    seed_node_ids: tuple[str, ...] = ()
    resource_ids: tuple[str, ...] | None = None
    node_categories: tuple[str, ...] = ()
    relation_types: tuple[str, ...] = ()
    direction: TraversalDirection = TraversalDirection.BOTH
    max_depth: int = 1
    vector_top_n: int = 20
    rerank_candidate_n: int = 50
    top_k: int = 5
    plugin_id: str | None = None
    metadata_filter: BaseModel | None = None

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must not be empty")
        if not 0 <= self.max_depth <= 2:
            raise ValueError("max_depth must be between 0 and 2")
        if not 1 <= self.vector_top_n <= 100:
            raise ValueError("vector_top_n must be between 1 and 100")
        if not self.top_k <= self.rerank_candidate_n <= 200:
            raise ValueError("rerank_candidate_n must be between top_k and 200")
        if not 1 <= self.top_k <= 20:
            raise ValueError("top_k must be between 1 and 20")
        if self.metadata_filter is not None and not self.plugin_id:
            raise ValueError("metadata_filter requires plugin_id")
        object.__setattr__(self, "query", self.query.strip())


@dataclass(frozen=True, slots=True)
class ChunkGraphHit:
    """由 LLM 图元 Evidence 回查出的权威正文 Chunk。"""

    chunk_id: str
    resource_id: str
    content_revision: str
    section_id: str | None
    section_path: tuple[str, ...]
    graph_ids: tuple[str, ...]
    rerank_score: float


@dataclass(frozen=True, slots=True)
class DeterministicGraphFactHit:
    """确定性 producer 生成、无需正文 Evidence 的可读图事实。"""

    target_type: Literal["node", "edge"]
    target_id: str
    resource_id: str
    content_revision: str
    producer_id: str
    rerank_score: float


GraphSearchHit = ChunkGraphHit | DeterministicGraphFactHit


@dataclass(frozen=True, slots=True)
class GraphSearchResult:
    """局部精排后的图谱检索结果；不暴露内部图遍历和向量 payload。"""

    hits: tuple[GraphSearchHit, ...]
    relevance_decision: str | None = None


def graph_node_id(*, category: str, name: str) -> str:
    """按类别和规范化名称生成可跨 revision 合并的稳定节点 ID。"""
    return "gn_" + _stable_hash(category, name)


def graph_edge_id(
    *,
    source_node_id: str,
    relation_type: str,
    target_node_id: str,
) -> str:
    """按有向端点和谓词生成稳定边 ID。"""
    return "ge_" + _stable_hash(source_node_id, relation_type, target_node_id)


def graph_evidence_id(
    *,
    target_type: Literal["node", "edge"],
    target_id: str,
    chunk_id: str,
    span: SourceSpan,
    quote_text: str,
) -> str:
    """同一图元在同一精确引文上的 Evidence 重建后保持稳定。"""
    return "gev_" + _stable_hash(
        target_type,
        target_id,
        chunk_id,
        str(span.start_offset),
        str(span.end_offset),
        quote_text,
    )


def graph_source_projection_id(
    *,
    target_type: Literal["node", "edge"],
    target_id: str,
    resource_id: str,
    content_revision: str,
    evidence_ids: tuple[str, ...] = (),
    producer_id: str | None = None,
) -> str:
    """为一个图元来源生成跨 Neo4j 与 Qdrant 共用的稳定身份。"""
    source = producer_id or "\0".join(sorted(evidence_ids))
    return "gsp_" + _stable_hash(
        target_type,
        target_id,
        resource_id,
        content_revision,
        source,
    )


def _stable_hash(*parts: str) -> str:
    normalized = "\0".join(" ".join(part.strip().casefold().split()) for part in parts)
    return sha256(normalized.encode("utf-8")).hexdigest()[:24]

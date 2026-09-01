"""图谱逻辑事实、来源投影和 Ontology 约束。"""

from __future__ import annotations

from collections.abc import Mapping
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


def _stable_hash(*parts: str) -> str:
    normalized = "\0".join(" ".join(part.strip().casefold().split()) for part in parts)
    return sha256(normalized.encode("utf-8")).hexdigest()[:24]

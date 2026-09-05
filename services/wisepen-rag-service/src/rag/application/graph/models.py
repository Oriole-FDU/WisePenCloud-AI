"""图谱逻辑事实、来源投影和稳定 ID。"""

from __future__ import annotations

from dataclasses import field
from enum import StrEnum
from hashlib import sha256
from typing import Literal

from common.utils.document import SourceSpan
from pydantic import BaseModel, ConfigDict, Field, model_validator

# --- 枚举与核心图元模型 ---

class GraphNodeKind(StrEnum):
    """节点在图中的资源语义。"""

    ENTITY = "entity"
    RESOURCE = "resource"
    EXTERNAL_RESOURCE = "external_resource"


class GraphNode(BaseModel):
    """与资源来源无关的逻辑实体。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(description="逻辑节点的稳定标识。")
    name: str = Field(description="节点的可读名称。")
    node_type: GraphNodeKind = Field(default=GraphNodeKind.ENTITY, description="节点的资源语义类型。")
    category: str = Field(description="节点在垂类本体中的类别。")
    description: str = Field(default="", description="节点的补充说明。")
    aliases: list[str] = Field(default_factory=list, description="节点的其他可检索名称。")
    extra_meta: dict[str, object] = Field(default_factory=dict, description="插件声明的附加属性。")


class GraphEdge(BaseModel):
    """与资源来源无关的逻辑关系。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    edge_id: str = Field(description="逻辑关系的稳定标识。")
    source_node_id: str = Field(description="关系起点节点标识。")
    target_node_id: str = Field(description="关系终点节点标识。")
    relation_type: str = Field(description="关系在垂类本体中的谓词。")
    description: str = Field(default="", description="关系的补充说明。")
    keywords: list[str] = Field(default_factory=list, description="关系相关的检索关键词。")
    extra_meta: dict[str, object] = Field(default_factory=dict, description="插件声明的附加属性。")


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
    source_spans: list[SourceSpan]
    quote_text: str

    @model_validator(mode="after")
    def _require_spans(self) -> TextGraphEvidence:
        if not self.source_spans:
            raise ValueError("TextGraphEvidence requires source_spans")
        if not self.quote_text:
            raise ValueError("TextGraphEvidence requires quote_text")
        return self


# --- 投影模型 ---

class GraphNodeProjection(BaseModel):
    """一个逻辑节点在某个 revision 的 LLM 或确定性来源投影。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node: GraphNode
    resource_id: str
    content_revision: str
    evidence_ids: list[str] = field(default_factory=list)
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
    evidence_ids: list[str] = field(default_factory=list)
    producer_id: str | None = None
    filter_values: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_source(self) -> GraphEdgeProjection:
        if bool(self.evidence_ids) == bool(self.producer_id):
            raise ValueError("graph projection requires exactly one source path")
        return self


# --- ID 生成工具函数 ---

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
    evidence_ids: list[str] | None = None,
    producer_id: str | None = None,
) -> str:
    """为一个图元来源生成跨 Neo4j 与 Qdrant 共用的稳定身份。"""
    source = producer_id or "\0".join(sorted(evidence_ids or []))
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

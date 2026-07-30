from __future__ import annotations

from dataclasses import dataclass

from chat.application.rag.graph_extraction import (
    KnowledgeEntityType,
    KnowledgeNodeKind,
    KnowledgeRelationType,
)


@dataclass(frozen=True, slots=True)
class KnowledgeNode:
    """稳定的图谱节点；同一节点可在多个窗口中以不同 label 出现。"""

    node_id: str  # 节点的稳定全局 ID，跨窗口和资源保持唯一。
    kind: KnowledgeNodeKind  # 节点种类：实体、资源、外部来源。
    label: str  # 节点的展示名，固定选用排序最小的等价形式。
    entity_type: KnowledgeEntityType | None = None  # 实体类型；外部来源与资源节点不设置。
    resource_id: str | None = None  # Resource 节点对应的私有资源 ID。


@dataclass(frozen=True, slots=True)
class KnowledgeMention:
    """实体节点在原文中的单次有证据出现。"""

    mention_id: str  # mention 的稳定 ID，绑定到关系版本。
    node_id: str  # 关联的实体节点 ID。
    chunk_id: str  # mention 所在的内容 chunk。
    source_ref_id: str  # mention 对应的 SourceRef ID。
    evidence_quote: str  # 经过 offset 校验的原文证据，用于 Agent 可读展示。


@dataclass(frozen=True, slots=True)
class KnowledgeEdge:
    """经过证据合并的稳定关系。"""

    edge_id: str  # 关系的稳定全局 ID，绑定到关系版本。
    source_node_id: str  # 关系源节点 ID。
    target_node_id: str  # 关系目标节点 ID。
    relation_type: KnowledgeRelationType  # 关系类型。
    predicate: str | None  # RELATED_TO 的具体谓词；其他关系为 None。
    evidence_quotes: tuple[str, ...]  # 经过 offset 校验的关系原文证据。
    evidence_source_ref_ids: tuple[str, ...]  # 用于内部正文回源的 SourceRef。


@dataclass(frozen=True, slots=True)
class KnowledgeGraphProjection:
    """资源级别的稳定知识图谱投影。"""

    resource_id: str  # 投影所属私有资源。
    content_revision: str  # 触发本次投影的内容版本。
    relation_revision: str  # 由抽取器、schema、关系版本共同派生的关系版本。
    nodes: tuple[KnowledgeNode, ...]  # 投影下全部稳定节点。
    mentions: tuple[KnowledgeMention, ...]  # 投影下全部 mention 出现。
    edges: tuple[KnowledgeEdge, ...]  # 投影下全部关系，evidence 已合并。

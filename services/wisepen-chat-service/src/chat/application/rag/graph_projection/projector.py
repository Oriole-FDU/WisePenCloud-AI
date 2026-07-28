from __future__ import annotations

import re
import unicodedata
from hashlib import sha256

from chat.application.rag.graph_extraction import (
    ExtractedKnowledgeNode,
    ExtractedKnowledgeRelation,
    KnowledgeNodeKind,
    KnowledgeRelationType,
    KnowledgeWindowExtraction,
)

from .models import KnowledgeEdge, KnowledgeGraphProjection, KnowledgeMention, KnowledgeNode


_EXTRACTOR_VERSION = "neo4j-graphrag:1.18.0"
_RELATION_SCHEMA_VERSION = "wisepen-knowledge-relations:v1"


def build_knowledge_graph_projection(
    *,
    resource_id: str,
    content_revision: str,
    extractions: tuple[KnowledgeWindowExtraction, ...],
) -> KnowledgeGraphProjection:
    """将窗口级抽取结果合并为稳定的知识图谱投影。"""
    # 抽取器、关系 schema 或内容变化时，生成新的关系版本。
    relation_revision = _stable_id(
        "rrel", resource_id, content_revision, _EXTRACTOR_VERSION, _RELATION_SCHEMA_VERSION
    )

    resource_node = KnowledgeNode(
        node_id=resource_node_id(resource_id),
        kind=KnowledgeNodeKind.RESOURCE,
        label=resource_id,
        resource_id=resource_id,
    )

    nodes: dict[str, KnowledgeNode] = {resource_node.node_id: resource_node}

    # 记录窗口内 local_id 对应的全局稳定节点 ID。
    local_node_ids: dict[tuple[str, str], str] = {}
    mentions: dict[str, KnowledgeMention] = {}

    for extraction in extractions:
        for candidate in extraction.nodes:
            node = _resolve_node(candidate, resource_id=resource_id)
            local_node_ids[(extraction.window.chunk_id, candidate.local_id)] = node.node_id

            # 同一节点可能在多个窗口中使用不同大小写或形式出现，固定选择排序最小的 label，
            # 保证结果不受抽取顺序影响。
            existing = nodes.get(node.node_id)
            if existing is None or _label_sort_key(node.label) < _label_sort_key(existing.label):
                nodes[node.node_id] = node

            evidence = candidate.evidence
            if evidence is None or node.kind is KnowledgeNodeKind.RESOURCE:
                continue

            # Mention 表示实体节点在原始文档中的一次有证据出现。
            mention_id = _stable_id(
                "knm", relation_revision, node.node_id, evidence.evidence_ref_id
            )
            mentions[mention_id] = KnowledgeMention(
                mention_id=mention_id,
                node_id=node.node_id,
                chunk_id=evidence.chunk_id,
                source_ref_id=evidence.source_ref_id,
                evidence_ref_id=evidence.evidence_ref_id,
                start_offset=evidence.start_offset,
                end_offset=evidence.end_offset,
            )

    # 将多个窗口中语义相同的关系合并，统一收集其证据。
    grouped_edges: dict[
        tuple[str, str, KnowledgeRelationType, str | None], list[ExtractedKnowledgeRelation]
    ] = {}

    for extraction in extractions:
        for relation in extraction.relations:
            source_node_id = local_node_ids.get((extraction.window.chunk_id, relation.source_local_id))
            target_node_id = local_node_ids.get((extraction.window.chunk_id, relation.target_local_id))

            # 关系端点未出现在当前窗口的节点结果中时，忽略该关系。
            if source_node_id is None or target_node_id is None:
                continue

            key = (source_node_id, target_node_id, relation.relation_type, relation.predicate)
            grouped_edges.setdefault(key, []).append(relation)

    edges: list[KnowledgeEdge] = []
    for (source_node_id, target_node_id, relation_type, predicate), relations in grouped_edges.items():
        relation_profiles = {relation.relation_profile for relation in relations}
        if len(relation_profiles) != 1:
            raise ValueError(f"relation {relation_type.value} has conflicting profiles")
        relation_profile = next(iter(relation_profiles))

        # 同一证据可以支持不同断言；仅合并完全相同的证据与断言。
        evidence_items = {
            (relation.evidence.evidence_ref_id, relation.assertion): relation for relation in relations
        }
        ordered = tuple(
            evidence_items[key] for key in sorted(evidence_items, key=lambda item: (item[0], item[1].value))
        )

        edge_id = _stable_id(
            "kne",
            relation_revision,
            source_node_id,
            target_node_id,
            relation_type.value,
            predicate or "",
        )

        edges.append(
            KnowledgeEdge(
                edge_id=edge_id,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                relation_type=relation_type,
                relation_profile=relation_profile,
                predicate=predicate,
                evidence_ref_ids=tuple(relation.evidence.evidence_ref_id for relation in ordered),
                evidence_source_ref_ids=tuple(relation.evidence.source_ref_id for relation in ordered),
                evidence_start_offsets=tuple(relation.evidence.start_offset for relation in ordered),
                evidence_end_offsets=tuple(relation.evidence.end_offset for relation in ordered),
                assertions=tuple(relation.assertion for relation in ordered),
            )
        )

    return KnowledgeGraphProjection(
        resource_id=resource_id,
        content_revision=content_revision,
        relation_revision=relation_revision,
        extractor_version=_EXTRACTOR_VERSION,
        nodes=tuple(nodes[node_id] for node_id in sorted(nodes)),
        mentions=tuple(mentions[mention_id] for mention_id in sorted(mentions)),
        edges=tuple(sorted(edges, key=lambda edge: edge.edge_id)),
    )


def resource_node_id(resource_id: str) -> str:
    """生成资源节点的稳定 ID。"""
    return _stable_id("kn", "resource", resource_id)


def _resolve_node(candidate: ExtractedKnowledgeNode, *, resource_id: str) -> KnowledgeNode:
    """将窗口内候选节点解析为全局稳定节点。"""
    if candidate.kind is KnowledgeNodeKind.RESOURCE:
        return KnowledgeNode(
            node_id=resource_node_id(resource_id),
            kind=KnowledgeNodeKind.RESOURCE,
            label=resource_id,
            resource_id=resource_id,
        )

    canonical_key = _canonical_key(candidate.label)

    if candidate.kind is KnowledgeNodeKind.EXTERNAL_SOURCE:
        return KnowledgeNode(
            node_id=_stable_id("kn", "external_source", canonical_key),
            kind=candidate.kind,
            label=candidate.label,
            source_key=canonical_key,
        )

    if candidate.entity_type is None:
        raise ValueError("entity candidate must provide entity_type")

    return KnowledgeNode(
        node_id=_stable_id("kn", "entity", candidate.entity_type.value, canonical_key),
        kind=candidate.kind,
        label=candidate.label,
        canonical_key=canonical_key,
        entity_type=candidate.entity_type,
    )


def _canonical_key(label: str) -> str:
    """归一化节点名称，用于实体合并和稳定 ID 生成。"""
    normalized = unicodedata.normalize("NFKC", label).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _label_sort_key(label: str) -> tuple[str, str]:
    """生成确定性的 label 排序键。"""
    return label.casefold(), label


def _stable_id(prefix: str, *parts: str) -> str:
    """根据有序字段生成固定长度的稳定 ID。"""
    digest = sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:32]}"

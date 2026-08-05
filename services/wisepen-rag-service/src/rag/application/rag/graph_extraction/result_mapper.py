from __future__ import annotations

from hashlib import sha256

from neo4j_graphrag.experimental.components.types import Neo4jGraph, Neo4jNode

from .models import (
    ExtractedKnowledgeNode,
    ExtractedKnowledgeRelation,
    KnowledgeAssertion,
    KnowledgeEntityType,
    KnowledgeEvidence,
    KnowledgeExtractionWindow,
    KnowledgeNodeKind,
    KnowledgeRelationType,
    KnowledgeWindowExtraction,
)
from .relations import relation_pattern_allowed


class KnowledgeGraphResultMapper:
    """将 SDK 候选图收紧为能够精确回源的业务抽取结果。"""

    __slots__ = ("_active_relations",)

    def __init__(self, active_relations: frozenset[KnowledgeRelationType]) -> None:
        self._active_relations = active_relations

    def map(self, graph: Neo4jGraph, window: KnowledgeExtractionWindow) -> KnowledgeWindowExtraction:
        """校验候选节点与关系，并为有效结果绑定原文证据。"""
        # SDK 使用 window_id 作为节点 ID 命名空间。parent 内滑窗不会互相串图。
        node_id_prefix = f"{window.window_id}:"
        candidate_nodes = {
            node.id: node for node in graph.nodes if node.id.startswith(node_id_prefix)
        }

        nodes = {
            node_id: validated_node
            for node_id, node in candidate_nodes.items()
            if (validated_node := self._validate_node(node, window)) is not None
        }

        # 使用稳定业务字段去重，避免 SDK 重复返回等价关系。
        relations: dict[tuple[object, ...], ExtractedKnowledgeRelation] = {}

        for relation in graph.relationships:
            if not relation.start_node_id.startswith(node_id_prefix):
                continue

            source = nodes.get(relation.start_node_id)
            target = nodes.get(relation.end_node_id)
            if source is None or target is None:
                continue

            try:
                relation_type = KnowledgeRelationType(relation.type)
                assertion = KnowledgeAssertion(_required_string(relation.properties.get("assertion")))
            except (TypeError, ValueError):
                continue

            # 关系必须属于当前启用集合，并满足节点类型模式约束。
            if relation_type not in self._active_relations:
                continue
            if not relation_pattern_allowed(source.kind, relation_type, target.kind):
                continue
            if assertion is not KnowledgeAssertion.AFFIRMED:
                continue

            # 关系没有能够精确映射回原文的证据时，不进入业务结果。
            evidence = _locate_evidence(window, relation.properties.get("evidence_quote"))
            if evidence is None:
                continue

            predicate = _optional_string(relation.properties.get("predicate"))

            # RELATED_TO 语义过于宽泛，必须由 predicate 进一步限定。
            if relation_type is KnowledgeRelationType.RELATED_TO and predicate is None:
                continue

            validated_relation = ExtractedKnowledgeRelation(
                source_local_id=source.local_id,
                target_local_id=target.local_id,
                relation_type=relation_type,
                evidence=evidence,
                predicate=predicate,
            )

            relation_key = (
                source.local_id,
                target.local_id,
                relation_type,
                predicate,
                evidence.evidence_ref_id,
            )
            relations[relation_key] = validated_relation

        return KnowledgeWindowExtraction(
            window=window,
            nodes=tuple(nodes.values()),
            relations=tuple(relations.values()),
        )

    @staticmethod
    def _validate_node(node: Neo4jNode, window: KnowledgeExtractionWindow) -> ExtractedKnowledgeNode | None:
        """校验候选节点，并为需要回源的节点绑定证据。"""
        try:
            kind = KnowledgeNodeKind(node.label)
            label = _required_string(node.properties.get("name"))
        except (TypeError, ValueError):
            return None

        # Resource 节点表示当前文档本身，不要求独立引文，但其 resource_id 必须与当前窗口严格一致。
        if kind is KnowledgeNodeKind.RESOURCE:
            if node.properties.get("resource_id") != window.resource_id:
                return None
            return ExtractedKnowledgeNode(local_id=node.id, kind=kind, label=label)

        # 其余节点必须具有能够精确映射回原文的证据。
        evidence = _locate_evidence(window, node.properties.get("evidence_quote"))
        if evidence is None:
            return None

        # ExternalSource 表示文中提及的外部来源，不属于普通知识实体，因此不要求 entity_type。
        if kind is KnowledgeNodeKind.EXTERNAL_SOURCE:
            return ExtractedKnowledgeNode(local_id=node.id, kind=kind, label=label, evidence=evidence)

        try:
            entity_type = KnowledgeEntityType(_required_string(node.properties.get("entity_type")))
        except (TypeError, ValueError):
            return None

        return ExtractedKnowledgeNode(
            local_id=node.id, kind=kind, label=label, entity_type=entity_type, evidence=evidence
        )


def _locate_evidence(window: KnowledgeExtractionWindow, raw_quote: object) -> KnowledgeEvidence | None:
    """定位窗口内引文，并通过显式映射恢复原文位置。"""
    quote = _optional_string(raw_quote)
    if quote is None:
        return None

    search_start = 0
    # 同一段引文可能在窗口内出现多次。逐个检查，直到找到能够完整落入某条映射的匹配位置。
    while True:
        local_start = window.current_text.find(quote, search_start)
        if local_start < 0:
            return None

        local_end = local_start + len(quote)
        source_span = _map_local_span(window, local_start=local_start, local_end=local_end)

        if source_span is not None:
            source_start, source_end = source_span

            # parent 证据可能跨多个 retrieval child，收集所有相交 SourceRef 用于后续回源。
            matching_source_refs = [
                source_ref
                for source_ref in window.source_refs
                for span in source_ref.source_spans
                if span.start_offset < source_end and span.end_offset > source_start
            ]

            if matching_source_refs:
                source_ref_ids = tuple(
                    sorted({source_ref.ref_id for source_ref in matching_source_refs})
                )

                identity = "\0".join(
                    (
                        window.resource_id,
                        str(window.document_version),
                        window.parent_id,
                        str(source_start),
                        str(source_end),
                        quote,
                    )
                )

                return KnowledgeEvidence(
                    evidence_ref_id="knev_" + sha256(identity.encode("utf-8")).hexdigest()[:32],
                    source_ref_ids=source_ref_ids,
                    parent_id=window.parent_id,
                    quote=quote,
                )

        # 使用 +1 支持引文在窗口文本中的重叠匹配。
        search_start = local_start + 1


def _map_local_span(
        window: KnowledgeExtractionWindow, *, local_start: int, local_end: int
) -> tuple[int, int] | None:
    """通过窗口预计算映射，将局部 span 转换为原文 span。"""
    # 映射的合法性和互斥性由窗口构建阶段负责验证。Mapper 只接受完整落入单条 local span 的引文，
    # 不再推断 current_text 的拼接方式或分隔符长度。
    for mapping in window.source_mappings:
        if local_start >= mapping.local_start and local_end <= mapping.local_end:
            source_start = mapping.source_start + local_start - mapping.local_start
            source_end = mapping.source_end - mapping.local_end + local_end
            if source_end - source_start == local_end - local_start:
                return source_start, source_end

    return None


def _required_string(value: object) -> str:
    """读取并规范化必填字符串。"""
    result = _optional_string(value)
    if result is None:
        raise ValueError("required string is missing")
    return result


def _optional_string(value: object) -> str | None:
    """读取可选字符串，并将空白字符串归一化为 None。"""
    if not isinstance(value, str):
        return None
    result = value.strip()
    return result or None

"""把窗口级知识候选规范化并合并为稳定的资源知识图谱。

合并流程的核心目标：
1. 把不同窗口抽取出的 ExtractedKnowledgeNode / ExtractedKnowledgeRelation
   规范化为统一的 KnowledgeNode / KnowledgeRelation，按 canonical key 去重。
2. 保留每条知识对应的完整 GraphEvidence，不拆散引用、ReadingBlock 与原文坐标。
3. 通过序列化后的稳定哈希生成 graph_revision，使图谱内容可比对、可缓存。
"""

import json
import re
import unicodedata
from hashlib import sha256

from rag.application.rag.index.graph.extractor import (
    ExtractedKnowledgeNode,
    ExtractedKnowledgeRelation,
    KnowledgeWindowExtraction,
)
from rag.domain.models.graph import (
    GraphEvidence,
    KnowledgeGraph,
    KnowledgeMention,
    KnowledgeNode,
    KnowledgeNodeKind,
    KnowledgeRelation,
    KnowledgeRelationType,
)

# 用于在 graph_facts 中标记当前合并产物的版本，参与 graph_revision 计算，
# 便于在 schema 变更时让旧 revision 失效。
_GRAPH_MERGE_VERSION = "knowledge-graph-merge:v2"


def merge_candidate_graph(
    *,
    resource_id: str,
    content_revision: str,
    extractions: list[KnowledgeWindowExtraction],
) -> KnowledgeGraph:
    """合并同一资源 revision 的已校验窗口候选。"""
    # 节点表：node_id -> KnowledgeNode。先把资源自身作为根节点放入。
    nodes = {
        resource_node_id(resource_id): KnowledgeNode(
            node_id=resource_node_id(resource_id),
            kind=KnowledgeNodeKind.RESOURCE,
            label=resource_id,
            resource_id=resource_id,
        )
    }
    # mention 索引：(node_id, evidence_id) -> evidence。
    # 同一个节点可能在多个窗口被提及，evidence_id 用于区分不同的引用来源。
    mentions: dict[tuple[str, str], GraphEvidence] = {}
    # 关系分组：按 (source, target, type, predicate_key) 聚合同一对节点间的同型关系，
    # 用于后续合并 evidence、生成稳定 edge_id。
    grouped_relations: dict[
        tuple[str, str, KnowledgeRelationType, str | None],
        list[ExtractedKnowledgeRelation],
    ] = {}

    for extraction in extractions:
        # local_id -> 全局 node_id 的映射表，供该窗口内的 relation 引用解析。
        local_node_ids: dict[str, str] = {}
        for candidate in extraction.nodes:
            node = _canonical_node(candidate, resource_id)
            local_node_ids[candidate.local_id] = node.node_id
            existing = nodes.get(node.node_id)
            # 同一 canonical 节点可能被多次抽取，保留 label 字典序最小者以保证确定性。
            if existing is None or _label_sort_key(node.label) < _label_sort_key(
                existing.label
            ):
                nodes[node.node_id] = node

            # 资源节点本身不收集 evidence（避免把资源根节点当作可被引用的实体）。
            if candidate.evidence is not None and node.kind is not KnowledgeNodeKind.RESOURCE:
                mentions[(node.node_id, candidate.evidence.evidence_id)] = (
                    candidate.evidence
                )

        for relation in extraction.relations:
            source_node_id = local_node_ids.get(relation.source_local_id)
            target_node_id = local_node_ids.get(relation.target_local_id)
            # 引用的端点未在该窗口出现则丢弃该关系（无法稳定解析端点）。
            if source_node_id is None or target_node_id is None:
                continue
            # RELATED_TO 关系使用规范化 predicate 作为分组键，使同义谓词合并；
            # 其它关系类型不使用 predicate_key（保持类型本身的区分）。
            predicate_key = (
                _normalize_label(relation.predicate).casefold()
                if relation.relation_type is KnowledgeRelationType.RELATED_TO
                and relation.predicate is not None
                else None
            )
            grouped_relations.setdefault(
                (
                    source_node_id,
                    target_node_id,
                    relation.relation_type,
                    predicate_key,
                ),
                [],
            ).append(relation)

    # 节点按 node_id 字典序输出，确保图谱内容稳定。
    ordered_nodes = [nodes[node_id] for node_id in sorted(nodes)]
    # graph_facts 是参与 graph_revision 计算的“事实摘要”，包含全部节点/mention/关系。
    # 任何字段变化都会让 revision 改变，从而触发下游重建。
    graph_facts = {
        "version": _GRAPH_MERGE_VERSION,
        "resource_id": resource_id,
        "content_revision": content_revision,
        "nodes": [
            {
                "node_id": node.node_id,
                "kind": node.kind.value,
                "label": node.label,
                "entity_type": (
                    node.entity_type.value if node.entity_type is not None else None
                ),
                "resource_id": node.resource_id,
            }
            for node in ordered_nodes
        ],
        "mentions": [
            {
                "node_id": node_id,
                "evidence_id": evidence_id,
                "resource_id": evidence.resource_id,
                "content_revision": evidence.content_revision,
                "reading_block_id": evidence.reading_block_id,
                "source_span": [
                    evidence.source_span.start_offset,
                    evidence.source_span.end_offset,
                ],
                "quote": evidence.quote,
            }
            for (node_id, evidence_id), evidence in sorted(mentions.items())
        ],
        "relations": [
            _relation_fact(key, relations)
            for key, relations in sorted(
                grouped_relations.items(),
                key=lambda item: (
                    item[0][0],
                    item[0][1],
                    item[0][2].value,
                    item[0][3] or "",
                ),
            )
        ],
    }
    graph_revision = _stable_id(
        "kgr",
        json.dumps(
            graph_facts,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )

    return KnowledgeGraph(
        resource_id=resource_id,
        content_revision=content_revision,
        graph_revision=graph_revision,
        nodes=ordered_nodes,
        mentions=[
            # mention_id 由 graph_revision + node + evidence 共同决定，
            # 因此图谱内容变化时所有 mention_id 也会同步刷新。
            KnowledgeMention(
                mention_id=_stable_id(
                    "knm",
                    graph_revision,
                    node_id,
                    evidence_id,
                ),
                node_id=node_id,
                evidence=evidence,
            )
            for (node_id, evidence_id), evidence in sorted(mentions.items())
        ],
        relations=[
            _merged_relation(graph_revision, key, relations)
            for key, relations in sorted(
                grouped_relations.items(),
                key=lambda item: (
                    item[0][0],
                    item[0][1],
                    item[0][2].value,
                    item[0][3] or "",
                ),
            )
        ],
    )


def _canonical_node(
    candidate: ExtractedKnowledgeNode,
    resource_id: str,
) -> KnowledgeNode:
    """把窗口候选节点规范化为全局稳定的 KnowledgeNode。"""
    if candidate.kind is KnowledgeNodeKind.RESOURCE:
        return KnowledgeNode(
            node_id=resource_node_id(resource_id),
            kind=KnowledgeNodeKind.RESOURCE,
            label=resource_id,
            resource_id=resource_id,
        )

    label = _normalize_label(candidate.label)
    canonical_key = label.casefold()
    if candidate.kind is KnowledgeNodeKind.EXTERNAL_SOURCE:
        return KnowledgeNode(
            node_id=_stable_id("kn", "external_source", canonical_key),
            kind=candidate.kind,
            label=label,
        )
    if candidate.entity_type is None:
        raise ValueError("entity candidate must provide entity_type")
    return KnowledgeNode(
        node_id=_stable_id(
            "kn",
            "entity",
            candidate.entity_type.value,
            canonical_key,
        ),
        kind=candidate.kind,
        label=label,
        entity_type=candidate.entity_type,
    )


def _relation_fact(
    key: tuple[str, str, KnowledgeRelationType, str | None],
    relations: list[ExtractedKnowledgeRelation],
) -> dict[str, object]:
    """把同一分组的关系序列化为参与 graph_revision 计算的事实字典。。"""
    source_node_id, target_node_id, relation_type, predicate_key = key
    evidence = {
        relation.evidence.evidence_id: relation.evidence for relation in relations
    }
    return {
        "source_node_id": source_node_id,
        "target_node_id": target_node_id,
        "relation_type": relation_type.value,
        "predicate": _relation_predicate(predicate_key, relations),
        "evidence": [
            {
                "evidence_id": evidence_id,
                "resource_id": item.resource_id,
                "content_revision": item.content_revision,
                "reading_block_id": item.reading_block_id,
                "source_span": [
                    item.source_span.start_offset,
                    item.source_span.end_offset,
                ],
                "quote": item.quote,
            }
            for evidence_id, item in sorted(evidence.items())
        ],
    }


def _merged_relation(
    graph_revision: str,
    key: tuple[str, str, KnowledgeRelationType, str | None],
    relations: list[ExtractedKnowledgeRelation],
) -> KnowledgeRelation:
    """把同一分组的关系合并为一条 KnowledgeRelation。"""
    source_node_id, target_node_id, relation_type, predicate_key = key
    evidence = {
        relation.evidence.evidence_id: relation.evidence for relation in relations
    }
    predicate = _relation_predicate(predicate_key, relations)
    return KnowledgeRelation(
        edge_id=_stable_id(
            "kne",
            graph_revision,
            source_node_id,
            target_node_id,
            relation_type.value,
            predicate_key or "",
        ),
        source_node_id=source_node_id,
        target_node_id=target_node_id,
        relation_type=relation_type,
        predicate=predicate,
        evidence=[item for _, item in sorted(evidence.items())],
    )


def _relation_predicate(
    predicate_key: str | None,
    relations: list[ExtractedKnowledgeRelation],
) -> str | None:
    """从分组关系中选出代表 predicate 文本。

    当 predicate_key 为 None（非 RELATED_TO 类型）时直接返回 None；
    否则在所有非空 predicate 中取规范化后的字典序最小者，保证确定性。
    """
    if predicate_key is None:
        return None
    return min(
        _normalize_label(relation.predicate)
        for relation in relations
        if relation.predicate is not None
    )


def _normalize_label(label: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", label)).strip()


def _label_sort_key(label: str) -> tuple[str, str]:
    """节点 label 的排序键：先按 casefold 排序，再按原始 label 二次排序。

    双层排序保证大小写不同但语义相同的 label 相邻，同时保留原始大小写差异
    作为 tiebreaker 以确保完全确定性。
    """
    return label.casefold(), label


def _stable_id(prefix: str, *parts: str) -> str:
    """基于多个字符串部件生成稳定短哈希 ID。"""
    digest = sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:16]}"


def resource_node_id(resource_id: str) -> str:
    """返回 Resource 节点在图事实和 Neo4j 中共用的稳定 ID。"""
    digest = sha256(f"resource\0{resource_id}".encode()).hexdigest()
    return f"kn_{digest[:16]}"

"""将候选图收紧为能够精确回源的领域候选。"""

from enum import StrEnum
from hashlib import sha256

from neo4j_graphrag.experimental.components.types import Neo4jGraph, Neo4jNode

from rag.domain.models.graph import (
    GraphEvidence,
    KnowledgeEntityType,
    KnowledgeNodeKind,
    KnowledgeRelationType,
)
from common.utils.document import SourceSpan

from .extractor import KnowledgeWindowExtraction, ExtractedKnowledgeNode, ExtractedKnowledgeRelation
from .relations import relation_pattern_allowed
from .windows import KnowledgeExtractionWindow


class KnowledgeAssertion(StrEnum):
    """GraphRAG 候选关系声明的校验词汇。"""

    AFFIRMED = "affirmed"
    NEGATED = "negated"
    CONDITIONAL = "conditional"
    UNCERTAIN = "uncertain"


class KnowledgeCandidateValidator:
    """验证节点、关系、断言和连续原文证据；非法候选直接丢弃。

    校验失败时不抛错，而是跳过该候选（返回 None 或不加入结果），
    因为 LLM 输出本身具有不确定性，应当容忍少量错误而非中断整次抽取。
    """

    __slots__ = ("_relations",)

    def __init__(self, relations: frozenset[KnowledgeRelationType]) -> None:
        # 允许的关系类型白名单；不在白名单内的关系直接丢弃。
        self._relations = relations

    def validate(
        self,
        graph: Neo4jGraph,
        window: KnowledgeExtractionWindow,
    ) -> KnowledgeWindowExtraction:
        """校验候选图并返回当前窗口的 KnowledgeWindowExtraction。"""
        prefix = f"{window.window_id}:"
        nodes = {
            node.id: validated
            for node in graph.nodes
            if node.id.startswith(prefix)
            if (validated := self._validate_node(node, window)) is not None
        }
        relations: dict[tuple[object, ...], ExtractedKnowledgeRelation] = {}
        for candidate in graph.relationships:
            source = nodes.get(candidate.start_node_id)
            target = nodes.get(candidate.end_node_id)
            # 端点未通过校验的关系直接丢弃（无法稳定合并）。
            if source is None or target is None:
                continue
            try:
                relation_type = KnowledgeRelationType(candidate.type)
                # assertion 必须是合法枚举字符串；非 affirmed 的关系会被丢弃
                # （negated/conditional/uncertain 不进入图谱）。
                assertion = KnowledgeAssertion(
                    _required_text(candidate.properties.get("assertion"))
                )
            except (TypeError, ValueError):
                continue
            if relation_type not in self._relations:
                continue
            # 校验端点类型组合是否合法（如 RESOURCE→EXTERNAL_SOURCE 仅允许少量关系）。
            if not relation_pattern_allowed(source.kind, relation_type, target.kind):
                continue
            if assertion is not KnowledgeAssertion.AFFIRMED:
                continue

            # 把 evidence_quote 映射回原文坐标；映射失败的关系直接丢弃。
            evidence = _locate_evidence(
                window,
                candidate.properties.get("evidence_quote"),
            )
            if evidence is None:
                continue
            predicate = _optional_text(candidate.properties.get("predicate"))
            # RELATED_TO 必须带具体 predicate（否则无法区分关系语义）。
            if relation_type is KnowledgeRelationType.RELATED_TO and predicate is None:
                continue

            relation = ExtractedKnowledgeRelation(
                source_local_id=source.local_id,
                target_local_id=target.local_id,
                relation_type=relation_type,
                evidence=evidence,
                predicate=predicate,
            )
            # 同一窗口内语义相同（含 evidence_id）的关系只保留最后一条；
            # evidence_id 已经包含 quote 与 span，因此重复的 evidence 会被自然合并。
            relations[
                (
                    source.local_id,
                    target.local_id,
                    relation_type,
                    predicate,
                    evidence.evidence_id,
                )
            ] = relation
        return KnowledgeWindowExtraction(
            resource_id=window.resource_id,
            content_revision=window.content_revision,
            nodes=list(nodes.values()),
            relations=list(relations.values()),
        )

    @staticmethod
    def _validate_node(
        node: Neo4jNode,
        window: KnowledgeExtractionWindow,
    ) -> ExtractedKnowledgeNode | None:
        """校验单个节点并转换为 ExtractedKnowledgeNode。"""
        try:
            kind = KnowledgeNodeKind(node.label)
            label = _required_text(node.properties.get("name"))
        except (TypeError, ValueError):
            return None
        if kind is KnowledgeNodeKind.RESOURCE:
            # RESOURCE 节点的 resource_id 必须与窗口归属一致，防止模型臆造其它资源。
            if node.properties.get("resource_id") != window.resource_id:
                return None
            return ExtractedKnowledgeNode(local_id=node.id, kind=kind, label=label)

        # 非 RESOURCE 节点必须有可定位的 evidence_quote。
        evidence = _locate_evidence(
            window,
            node.properties.get("evidence_quote"),
        )
        if evidence is None:
            return None
        if kind is KnowledgeNodeKind.EXTERNAL_SOURCE:
            return ExtractedKnowledgeNode(
                local_id=node.id,
                kind=kind,
                label=label,
                evidence=evidence,
            )
        # ENTITY 类型必须有合法的 entity_type 枚举值。
        try:
            entity_type = KnowledgeEntityType(
                _required_text(node.properties.get("entity_type"))
            )
        except (TypeError, ValueError):
            return None
        return ExtractedKnowledgeNode(
            local_id=node.id,
            kind=kind,
            label=label,
            entity_type=entity_type,
            evidence=evidence,
        )


def _locate_evidence(
    window: KnowledgeExtractionWindow,
    raw_quote: object,
) -> GraphEvidence | None:
    """在窗口文本中定位 quote，并记录其 ReadingBlock 与原文坐标。
    返回 None 表示 quote 不是窗口文本的连续子串，或无法稳定映射到原文。
    """
    quote = _optional_text(raw_quote)
    if quote is None:
        return None

    search_start = 0
    while True:
        local_start = window.text.find(quote, search_start)
        if local_start < 0:
            return None
        local_end = local_start + len(quote)
        source_span = _map_source_span(window, local_start, local_end)
        if source_span is not None:
            identity = (
                f"{window.resource_id}\0{window.content_revision}\0"
                f"{window.reading_block_id}\0{source_span.start_offset}\0"
                f"{source_span.end_offset}\0{quote}"
            )
            return GraphEvidence(
                evidence_id=(
                    "knev_" + sha256(identity.encode("utf-8")).hexdigest()[:16]
                ),
                resource_id=window.resource_id,
                content_revision=window.content_revision,
                reading_block_id=window.reading_block_id,
                source_span=source_span,
                quote=quote,
            )
        # 同一 quote 可能出现多次，继续寻找能够完整落入一条映射的匹配。
        search_start = local_start + 1


def _map_source_span(
    window: KnowledgeExtractionWindow,
    local_start: int,
    local_end: int,
) -> SourceSpan | None:
    """把窗口局部坐标区间映射回原文 SourceSpan。"""
    for mapping in window.source_mappings:
        if local_start < mapping.window_start or local_end > mapping.window_end:
            continue
        # 原文起点 = mapping 原文起点 + (局部起点 - mapping 局部起点)
        source_start = (
            mapping.source_span.start_offset + local_start - mapping.window_start
        )
        return SourceSpan(source_start, source_start + local_end - local_start)
    return None


def _required_text(value: object) -> str:
    """要求 value 必须是非空字符串，否则抛 ValueError。

    用于 schema 中强制要求的字段（如 name、assertion、entity_type）。
    """
    result = _optional_text(value)
    if result is None:
        raise ValueError("required string is missing")
    return result


def _optional_text(value: object) -> str | None:
    """把 value 规范化为非空 stripped 字符串或 None。

    - 非 str 类型直接返回 None。
    - strip 后为空字符串也返回 None，便于上层用 is None 统一判断“缺失”。
    """
    if not isinstance(value, str):
        return None
    return value.strip() or None

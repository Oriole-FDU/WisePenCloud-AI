from __future__ import annotations

from hashlib import sha256

from neo4j_graphrag import __version__ as neo4j_graphrag_version
from neo4j_graphrag.experimental.components.entity_relation_extractor import LLMEntityRelationExtractor, OnError
from neo4j_graphrag.experimental.components.schema import (
    ConstraintType,
    GraphConstraintType,
    GraphSchema,
    NodeType,
    Pattern,
    PropertyType,
    RelationshipType,
)
from neo4j_graphrag.experimental.components.types import Neo4jGraph, TextChunk, TextChunks
from neo4j_graphrag.llm.base import LLMInterfaceV2

from rag.application.rag.repositories import KnowledgeGraphExtractionCache
from .cache_codec import decode_cached_graph, encode_cached_graph, slice_window_graph
from .models import (
    KnowledgeEntityType,
    KnowledgeExtractionWindow,
    KnowledgeNodeKind,
    KnowledgeRelationProfile,
    KnowledgeRelationType,
    KnowledgeWindowExtraction,
)
from .relations import relation_descriptions, relation_pattern_allowed
from .result_mapper import KnowledgeGraphResultMapper
from .windows import render_extraction_window

_REQUIRES_EXTRACTION_EXAMPLE = """
Input: Transformer requires positional encoding to represent token order.
Output: create Entity nodes for Transformer and positional encoding, then a REQUIRES relation with the exact sentence as evidence_quote.
"""

_NEGATIVE_EXTRACTION_EXAMPLE = """
Input: The note discusses Alpha and Beta, but states no relationship between them.
Output: do not create a relation.
"""

# Schema、抽取示例和 SDK 版本会自动进入缓存契约。
# 若其他规则改变 SDK 原始候选图却未反映在这些输入中，必须升级该版本；
# Mapper 的纯后处理变化不需要升级，因为缓存命中后仍会重新执行 Mapper。
_EXTRACTION_CACHE_VERSION = "knowledge_graph_extraction:v1"


class KnowledgeGraphExtractor:
    """调用 Neo4j GraphRAG SDK，并只保留能够精确回源的候选图。"""

    __slots__ = (
        "_active_relations",
        "_cache",
        "_cache_contract_hash",
        "_cache_profile",
        "_examples",
        "_extractor",
        "_result_mapper",
        "_schema",
    )

    def __init__(
            self,
            *,
            llm: LLMInterfaceV2,
            cache: KnowledgeGraphExtractionCache | None = None,
            cache_profile: str = "",
            profiles: frozenset[KnowledgeRelationProfile] | None = None,
            max_concurrency: int = 5,
    ) -> None:
        cache_profile = cache_profile.strip()
        if cache is not None and not cache_profile:
            raise ValueError("cache_profile is required when extraction cache is enabled")

        if profiles is None:
            profiles = frozenset(
                {
                    KnowledgeRelationProfile.CORE,
                    KnowledgeRelationProfile.LEARNING,
                    KnowledgeRelationProfile.SCHOLARLY,
                }
            )

        self._schema = _build_schema(profiles)
        self._cache = cache
        self._cache_profile = cache_profile

        # 实际启用关系以最终生成的 GraphSchema 为准，避免 Mapper 与 SDK 使用的关系集合发生偏差。
        self._active_relations = frozenset(
            KnowledgeRelationType(relationship.label) for relationship in self._schema.relationship_types
        )
        self._result_mapper = KnowledgeGraphResultMapper(self._active_relations)
        self._examples = _NEGATIVE_EXTRACTION_EXAMPLE
        if KnowledgeRelationType.REQUIRES in self._active_relations:
            self._examples = _REQUIRES_EXTRACTION_EXAMPLE + "\n" + _NEGATIVE_EXTRACTION_EXAMPLE
        self._cache_contract_hash = sha256(
            "\0".join(
                (
                    _EXTRACTION_CACHE_VERSION,
                    neo4j_graphrag_version,
                    self._schema.model_dump_json(exclude_none=True),
                    self._examples,
                )
            ).encode("utf-8")
        ).hexdigest()

        self._extractor = LLMEntityRelationExtractor(
            llm=llm,  # type: ignore[arg-type]
            create_lexical_graph=False,
            on_error=OnError.RAISE,
            max_concurrency=max_concurrency,
            use_structured_output=True,
        )

    async def extract(
            self, windows: tuple[KnowledgeExtractionWindow, ...]
    ) -> tuple[KnowledgeWindowExtraction, ...]:
        """批量抽取窗口知识图，并恢复为输入窗口顺序。"""
        if not windows:
            return ()

        # 未配置缓存时，直接批量执行 SDK 抽取。
        if self._cache is None:
            graph = await self._run_extractor(windows)
            return tuple(self._result_mapper.map(graph, window) for window in windows)

        cache_keys = tuple(self._cache_key(window) for window in windows)
        cached_payloads = await self._cache.get_many(cache_keys)

        # 使用输入下标保存结果，确保缓存命中与重新抽取混合时，最终结果仍严格对应原始 windows 顺序。
        results: dict[int, KnowledgeWindowExtraction] = {}
        missing: list[tuple[int, str, KnowledgeExtractionWindow]] = []

        for index, (cache_key, window) in enumerate(zip(cache_keys, windows, strict=True)):
            graph = decode_cached_graph(cached_payloads.get(cache_key), window.chunk_id)
            if graph is None:
                missing.append((index, cache_key, window))
                continue

            results[index] = self._result_mapper.map(graph, window)

        if missing:
            missing_windows = tuple(item[2] for item in missing)
            graph = await self._run_extractor(missing_windows)
            cache_values = {}

            for index, cache_key, window in missing:
                # SDK 返回的是多个窗口组成的聚合图。映射和缓存前先切出当前窗口所属的候选子图。
                window_graph = slice_window_graph(graph, window.chunk_id)
                results[index] = self._result_mapper.map(window_graph, window)
                cache_values[cache_key] = encode_cached_graph(window_graph, window.chunk_id)

            await self._cache.set_many(cache_values)

        return tuple(results[index] for index in range(len(windows)))

    async def _run_extractor(self, windows: tuple[KnowledgeExtractionWindow, ...]) -> Neo4jGraph:
        """将业务窗口转换为 SDK TextChunk，并执行批量图抽取。"""
        return await self._extractor.run(
            chunks=TextChunks(
                chunks=[
                    TextChunk(
                        uid=window.chunk_id,
                        index=window.chunk_index,
                        text=render_extraction_window(window),
                        metadata={
                            "resource_id": window.resource_id,
                            "content_revision": window.content_revision,
                        },
                    )
                    for window in windows
                ]
            ),
            schema=self._schema,
            examples=self._examples,
        )

    def _cache_key(self, window: KnowledgeExtractionWindow) -> str:
        """为影响候选图抽取结果的输入生成稳定缓存键。"""
        value = "\0".join(
            (self._cache_contract_hash, self._cache_profile, render_extraction_window(window))
        )
        return sha256(value.encode("utf-8")).hexdigest()


def _build_schema(profiles: frozenset[KnowledgeRelationProfile]) -> GraphSchema:
    """根据启用的关系 Profile 构建严格 GraphRAG Schema。"""
    descriptions = relation_descriptions(profiles)

    # 所有业务关系都必须携带断言状态和连续原文证据。
    # predicate 仅对 RELATED_TO 强制要求，最终由 Mapper 校验。
    evidence_properties = [
        PropertyType(
            name="evidence_quote",
            type="STRING",
            description="CURRENT_CHUNK 中支持该关系的连续原文",
        ),
        PropertyType(
            name="assertion",
            type="STRING",
            description="affirmed、negated、conditional 或 uncertain",
        ),
        PropertyType(
            name="predicate",
            type="STRING",
            description="RELATED_TO 的具体谓词，其他关系可省略",
        ),
    ]

    relationship_types = tuple(
        RelationshipType(
            label=relation.value,
            description=description,
            properties=evidence_properties,
            additional_properties=False,
        )
        for relation, description in descriptions.items()
    )

    # 只向 SDK 暴露业务层明确允许的 source-kind / relation / target-kind 组合。
    patterns = tuple(
        Pattern(source=source.value, relationship=relation.value, target=target.value)
        for source in KnowledgeNodeKind
        for relation in descriptions
        for target in KnowledgeNodeKind
        if relation_pattern_allowed(source, relation, target)
    )

    return GraphSchema(
        node_types=(
            NodeType(
                label=KnowledgeNodeKind.ENTITY.value,
                description="正文中可跨文档导航的通用实体",
                properties=[
                    PropertyType(name="name", type="STRING"),
                    PropertyType(
                        name="entity_type",
                        type="STRING",
                        description="实体类型，只能是 "
                                    + ", ".join(entity_type.value for entity_type in KnowledgeEntityType),
                    ),
                    PropertyType(
                        name="evidence_quote",
                        type="STRING",
                        description="CURRENT_CHUNK 中出现该实体的连续原文",
                    ),
                ],
                additional_properties=False,
            ),
            NodeType(
                label=KnowledgeNodeKind.RESOURCE.value,
                description="系统给出的 CURRENT_RESOURCE，只能表示当前私有资源",
                properties=[
                    PropertyType(name="name", type="STRING"),
                    PropertyType(name="resource_id", type="STRING"),
                ],
                additional_properties=False,
            ),
            NodeType(
                label=KnowledgeNodeKind.EXTERNAL_SOURCE.value,
                description="正文明确引用但尚未解析为私有 Resource 的来源",
                properties=[
                    PropertyType(name="name", type="STRING"),
                    PropertyType(
                        name="evidence_quote",
                        type="STRING",
                        description="CURRENT_CHUNK 中出现该来源的连续原文",
                    ),
                ],
                additional_properties=False,
            ),
        ),
        relationship_types=relationship_types,
        patterns=patterns,
        constraints=(
            # 不同类型节点的必要属性约束。
            *(
                ConstraintType(
                    type=GraphConstraintType.EXISTENCE,
                    property_names=(property_name,),
                    node_type=node_type.value,
                )
                for node_type, property_names in (
                (KnowledgeNodeKind.ENTITY, ("name", "entity_type", "evidence_quote")),
                (KnowledgeNodeKind.RESOURCE, ("name", "resource_id")),
                (KnowledgeNodeKind.EXTERNAL_SOURCE, ("name", "evidence_quote")),
            )
                for property_name in property_names
            ),
            # 所有关系都必须具有精确证据和断言状态。
            *(
                ConstraintType(
                    type=GraphConstraintType.EXISTENCE,
                    property_names=(property_name,),
                    relationship_type=relation.value,
                )
                for relation in descriptions
                for property_name in ("evidence_quote", "assertion")
            ),
        ),
        additional_node_types=False,
        additional_relationship_types=False,
        additional_patterns=False,
    )

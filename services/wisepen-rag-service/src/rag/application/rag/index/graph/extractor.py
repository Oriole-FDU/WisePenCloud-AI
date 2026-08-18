"""编排 GraphRAG 候选抽取、派生产物复用和确定性校验。。"""
from dataclasses import dataclass, field
from hashlib import sha256

from neo4j_graphrag import __version__ as graph_rag_version
from neo4j_graphrag.experimental.components.schema import (
    ConstraintType,
    GraphConstraintType,
    GraphSchema,
    NodeType,
    Pattern,
    PropertyType,
    RelationshipType,
)
from neo4j_graphrag.experimental.components.types import Neo4jGraph

from rag.domain.models.graph import (
    KnowledgeEntityType,
    KnowledgeNodeKind,
    KnowledgeRelationType, GraphEvidence,
)
from rag.domain.models.structure import StructureMode
from rag.domain.repositories.mongo import PublishedResourceReader
from rag.domain.repositories.mongo.generation_artifact_store import (
    GenerationArtifactStore,
)

# 注意：KnowledgeCandidateValidator 不能在模块级导入——candidate_validator 反向
# 依赖本模块的数据类，模块级互相导入会形成循环；在 __init__ 内延迟导入。
from .llm import GraphRagCandidateExtractor, QueryClientGraphRagLLM
from .relations import (
    KnowledgeRelationProfile,
    relation_descriptions,
    relation_pattern_allowed,
)
from .windows import (
    KnowledgeExtractionWindow,
    build_extraction_windows,
    render_extraction_window,
)

# 派生产物（SDK 原始候选图）的版本号；schema/编码规则变化时递增，使旧 artifact 失效。
_ARTIFACT_VERSION = "graph-candidates:v1"

@dataclass(slots=True)
class ExtractedKnowledgeRelation:
    source_local_id: str
    target_local_id: str
    relation_type: KnowledgeRelationType
    evidence: GraphEvidence
    predicate: str | None = None


@dataclass(slots=True)
class ExtractedKnowledgeNode:
    local_id: str
    kind: KnowledgeNodeKind
    label: str
    entity_type: KnowledgeEntityType | None = None
    evidence: GraphEvidence | None = None


@dataclass(slots=True)
class KnowledgeWindowExtraction:
    """一个抽取窗口经过确定性校验后的节点和关系候选。"""

    resource_id: str
    content_revision: str
    nodes: list[ExtractedKnowledgeNode] = field(default_factory=list)
    relations: list[ExtractedKnowledgeRelation] = field(default_factory=list)


class KnowledgeGraphExtractor:
    """抽取并校验窗口级知识候选，不负责合并或发布图谱。"""

    def __init__(
        self,
        *,
        llm: QueryClientGraphRagLLM,
        generation_artifact_store: GenerationArtifactStore,
        source_reader: PublishedResourceReader,
        max_concurrency: int = 5,
        profiles: frozenset[KnowledgeRelationProfile] | None = None,
    ) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        # 默认启用所有 profile；调用方可传入子集以限制抽取范围。
        profiles = profiles or frozenset(KnowledgeRelationProfile)
        # 根据 profile 构建 GraphRAG schema，决定允许的关系类型与端点组合。
        self._schema = self._build_schema(profiles)
        # 从 schema 中提取实际启用的关系类型，作为 validator 的白名单。
        active_relations = frozenset(
            KnowledgeRelationType(item.label)
            for item in self._schema.relationship_types
        )

        # 延迟导入以打破与 candidate_validator 的循环依赖（见文件头部说明）。
        from .candidate_validator import KnowledgeCandidateValidator

        self._validator = KnowledgeCandidateValidator(active_relations)
        self._candidate_extractor = GraphRagCandidateExtractor(
            llm=llm,
            max_concurrency=max_concurrency,
        )
        self._generation_artifact_store = generation_artifact_store
        self._source_reader = source_reader

        # 派生产物缓存的“契约哈希”
        self._store_contract = sha256(
            (
                f"{_ARTIFACT_VERSION}\0{graph_rag_version}\0{llm.artifact_profile}\0"
                f"{self._schema.model_dump_json(exclude_none=True)}"
            ).encode()
        ).hexdigest()

    async def extract(
        self,
        *,
        resource_id: str,
        content_revision: str,
    ) -> list[KnowledgeWindowExtraction]:
        """加载素材、构建窗口、抽取并校验候选，返回每个窗口的 KnowledgeWindowExtraction。

        非 SECTIONED 文档直接返回空列表（无章节上下文，不抽取图谱）。
        """
        source = await self._source_reader.get_graph_build_source(
            resource_id,
            content_revision,
        )
        if source.structure.mode is not StructureMode.SECTIONED:
            return []
        return await self._extract_windows(build_extraction_windows(source))


    async def _extract_windows(
        self,
        windows: list[KnowledgeExtractionWindow],
    ) -> list[KnowledgeWindowExtraction]:
        """对所有窗口执行抽取，命中缓存的窗口复用 SDK 原始候选，缺失的窗口重新抽取。"""
        if not windows:
            return []
        # 防御：所有窗口必须属于同一资源同一 revision，否则缓存键会错乱。
        resource_ids = {window.resource_id for window in windows}
        revisions = {window.content_revision for window in windows}
        if len(resource_ids) != 1 or len(revisions) != 1:
            raise ValueError("extraction windows must share one resource revision")

        resource_id = next(iter(resource_ids))
        artifact_keys = [self._artifact_key(window) for window in windows]
        # 批量读取已持久化的 SDK 原始候选图。
        stored_artifacts = await self._generation_artifact_store.get_many(
            resource_id=resource_id,
            artifact_kind="graph",
            artifact_keys=artifact_keys,
        )
        results: dict[int, KnowledgeWindowExtraction] = {}
        missing: list[tuple[int, str, KnowledgeExtractionWindow]] = []
        for index, (key, window) in enumerate(zip(artifact_keys, windows, strict=True)):
            graph = (
                _decode_candidate_graph(stored_artifacts[key], window.window_id)
                if key in stored_artifacts
                else None
            )
            if graph is None:
                missing.append((index, key, window))
                continue
            # 派生产物只保存 SDK 原始候选；命中后仍执行当前版本的确定性校验。
            results[index] = self._validator.validate(graph, window)

        if missing:
            # 缺失的窗口一次性送入 SDK 抽取（内部并发）。
            missing_windows = [item[2] for item in missing]
            graph = await self._candidate_extractor.extract(
                missing_windows, self._schema
            )
            generated_artifacts: dict[str, str] = {}
            for index, key, window in missing:
                # SDK 返回的是合并图，按 window_id 切出当前窗口子图。
                window_graph = _slice_candidate_graph(graph, window.window_id)
                results[index] = self._validator.validate(window_graph, window)
                # 把切出的子图编码为 JSON 持久化，下次重复索引时复用。
                generated_artifacts[key] = _encode_candidate_graph(
                    window_graph,
                    window.window_id,
                )
            await self._generation_artifact_store.set_many(
                resource_id=resource_id,
                artifact_kind="graph",
                artifacts=generated_artifacts,
            )
        # 按窗口原始顺序返回，保证下游合并结果稳定。
        return [results[index] for index in range(len(windows))]


    def _artifact_key(self, window: KnowledgeExtractionWindow) -> str:
        """计算窗口的派生产物缓存键"""
        value = f"{self._store_contract}\0{render_extraction_window(window)}"
        return sha256(value.encode("utf-8")).hexdigest()


    @staticmethod
    def _build_schema(
        profiles: frozenset[KnowledgeRelationProfile],
    ) -> GraphSchema:
        """根据启用的 profile 构建 GraphRAG SDK 使用的 GraphSchema。"""
        descriptions = relation_descriptions(profiles)
        # 关系共有的“证据属性”：evidence_quote（原文引用）、assertion（断言）、
        # predicate（RELATED_TO 的具体谓词）。
        evidence_properties = [
            PropertyType(
                name="evidence_quote",
                type="STRING",
                description="current_reading_block 中支持关系的连续原文",
            ),
            PropertyType(
                name="assertion",
                type="STRING",
                description="affirmed、negated、conditional 或 uncertain",
            ),
            PropertyType(
                name="predicate",
                type="STRING",
                description="RELATED_TO 的具体谓词",
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
        # 枚举所有合法 (source, relation, target) 三元组，作为 SDK 的 pattern。
        patterns = tuple(
            Pattern(
                source=source.value,
                relationship=relation.value,
                target=target.value,
            )
            for source in KnowledgeNodeKind
            for relation in descriptions
            for target in KnowledgeNodeKind
            if relation_pattern_allowed(source, relation, target)
        )
        # 不同节点类型有不同的属性集合；ENTITY 必须有 entity_type，
        # RESOURCE 必须有 resource_id，EXTERNAL_SOURCE 与 ENTITY 必须有 evidence_quote。
        node_properties = {
            KnowledgeNodeKind.ENTITY: [
                PropertyType(name="name", type="STRING"),
                PropertyType(
                    name="entity_type",
                    type="STRING",
                    description=", ".join(item.value for item in KnowledgeEntityType),
                ),
                PropertyType(name="evidence_quote", type="STRING"),
            ],
            KnowledgeNodeKind.RESOURCE: [
                PropertyType(name="name", type="STRING"),
                PropertyType(name="resource_id", type="STRING"),
            ],
            KnowledgeNodeKind.EXTERNAL_SOURCE: [
                PropertyType(name="name", type="STRING"),
                PropertyType(name="evidence_quote", type="STRING"),
            ],
        }
        required_node_properties = {
            KnowledgeNodeKind.ENTITY: ("name", "entity_type", "evidence_quote"),
            KnowledgeNodeKind.RESOURCE: ("name", "resource_id"),
            KnowledgeNodeKind.EXTERNAL_SOURCE: ("name", "evidence_quote"),
        }
        return GraphSchema(
            node_types=tuple(
                NodeType(
                    label=kind.value,
                    description=kind.value,
                    properties=properties,
                    additional_properties=False,
                )
                for kind, properties in node_properties.items()
            ),
            relationship_types=relationship_types,
            patterns=patterns,
            # 存在性约束：必填字段必须出现，否则 SDK 校验失败。
            constraints=(
                *(
                    ConstraintType(
                        type=GraphConstraintType.EXISTENCE,
                        property_names=(property_name,),
                        node_type=kind.value,
                    )
                    for kind, property_names in required_node_properties.items()
                    for property_name in property_names
                ),
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


def _encode_candidate_graph(graph: Neo4jGraph, window_id: str) -> str:
    """把候选图的节点/关系 ID 前缀从 window_id: 转换为 stored:，序列化为 JSON。

    转换前缀后，存储的图不再绑定具体窗口身份，仅记录拓扑结构；
    后续读取时再按当前 window_id 还原前缀，便于校验。
    """
    prefix = f"{window_id}:"
    normalized = Neo4jGraph(
        nodes=[
            node.model_copy(
                update={"id": _replace_prefix(node.id, prefix, "stored:")}
            )
            for node in graph.nodes
        ],
        relationships=[
            relation.model_copy(
                update={
                    "start_node_id": _replace_prefix(
                        relation.start_node_id,
                        prefix,
                        "stored:",
                    ),
                    "end_node_id": _replace_prefix(
                        relation.end_node_id,
                        prefix,
                        "stored:",
                    ),
                }
            )
            for relation in graph.relationships
        ],
    )
    return normalized.model_dump_json()


def _decode_candidate_graph(payload: str, window_id: str) -> Neo4jGraph | None:
    """把 stored: 前缀还原为 window_id: 前缀，恢复节点 ID 的窗口归属。

    返回 None 表示 payload 不是合法的候选图 JSON（比如旧版本格式），
    调用方应据此重新触发抽取。
    """
    try:
        graph = Neo4jGraph.model_validate_json(payload)
        prefix = f"{window_id}:"
        return Neo4jGraph(
            nodes=[
                node.model_copy(
                    update={
                        "id": _replace_prefix(
                            node.id,
                            "stored:",
                            prefix,
                        )
                    }
                )
                for node in graph.nodes
            ],
            relationships=[
                relation.model_copy(
                    update={
                        "start_node_id": _replace_prefix(
                            relation.start_node_id,
                            "stored:",
                            prefix,
                        ),
                        "end_node_id": _replace_prefix(
                            relation.end_node_id,
                            "stored:",
                            prefix,
                        ),
                    }
                )
                for relation in graph.relationships
            ],
        )
    except ValueError:
        return None


def _slice_candidate_graph(graph: Neo4jGraph, window_id: str) -> Neo4jGraph:
    """从一次批量抽取的合并图中按 window_id 切出该窗口对应的子图。

    GraphRAG SDK 一次会处理多个窗口，输出合并图；本函数按 window_id: 前缀
    过滤节点，并保留两端节点都属于本窗口的关系，丢弃跨窗口关系（不合法）。
    """
    prefix = f"{window_id}:"
    nodes = [node for node in graph.nodes if node.id.startswith(prefix)]
    node_ids = {node.id for node in nodes}
    return Neo4jGraph(
        nodes=nodes,
        relationships=[
            relation
            for relation in graph.relationships
            if relation.start_node_id in node_ids
            and relation.end_node_id in node_ids
        ],
    )


def _replace_prefix(value: str, old: str, new: str) -> str:
    if not value.startswith(old):
        raise ValueError("graph node id does not match extraction window")
    return f"{new}{value[len(old):]}"

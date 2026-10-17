"""来源贡献与拓扑的读写端口，不解释本体、ACL 或发布状态。"""

import json
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from rag.core.schema import GraphEdge, GraphNode, Ontology
from rag.core.schema.filters import MetadataFilters
from rag.core.schema.graph import GraphDirection


@dataclass(frozen=True, slots=True)
class GraphSourceProjection:
    """一个 Chunk 的完整投影输入；空事实也表达移除该来源旧贡献。"""

    chunk_id: str
    metadata: dict[str, object]  # 资源/revision/ontology_name；后端执行普通属性过滤。
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)


class GraphReader(Protocol):
    async def get_nodes(
        self, node_ids: list[str], *, filters: MetadataFilters | None = None
    ) -> list[GraphNode]:
        """按请求 ID、来源 Chunk ID 排序返回来源事实，空 IDs 无 I/O。"""
        ...

    async def get_relations(
        self, edge_ids: list[str], *, filters: MetadataFilters | None = None
    ) -> list[GraphEdge]:
        """同一逻辑关系的各来源分别返回，描述不汇总。"""
        ...

    async def get_neighbors(
        self,
        node_ids: list[str],
        *,
        direction: GraphDirection = GraphDirection.OUTGOING,
        relation_types: list[str] | None = None,
        filters: MetadataFilters | None = None,
        limit: int | None = 100,
    ) -> list[GraphEdge]:
        """limit 限制逻辑关系数；None 完整定位连接，匹配来源始终完整返回。"""
        ...


class GraphWriter(Protocol):
    async def replace_sources(self, projections: list[GraphSourceProjection]) -> None:
        """只替换明确给出的来源；全部批次确认后返回，前批成功不补偿。"""
        ...

    async def delete_sources(self, filters: MetadataFilters) -> None:
        """非空条件删除来源贡献并清理受影响孤儿；不撤销资源可见性。"""
        ...


class GraphStorage(GraphReader, GraphWriter, Protocol):
    """一个实例绑定一个物理图，核心 ID 不包含后端图命名空间。"""

    async def ensure_schema(self) -> None:
        """显式初始化图存储所需的 schema；可重复调用，失败传播。"""
        ...


class GraphVectorKind(StrEnum):
    ENTITY = "entity"
    RELATIONSHIP = "relationship"


def graph_vector_record_id(kind: GraphVectorKind, chunk_id: str, fact_id: str) -> str:
    # 使用数组编码，来源与图 ID 内的分隔符不会改变向量身份的边界。
    identity = json.dumps(
        [kind, chunk_id, fact_id], ensure_ascii=False, separators=(",", ":")
    )
    return "gvec_" + sha256(identity.encode("utf-8")).hexdigest()


def graph_fact_sha256(fact: GraphNode | GraphEdge, nodes: dict[str, GraphNode]) -> str:
    """索引文本的事实输入摘要；查询无需加载本体配置即可核对是否过期。"""
    if isinstance(fact, GraphNode):
        content = [fact.id, fact.name, fact.description or ""]
    else:
        content = [
            fact.id,
            nodes[fact.source_id].name,
            nodes[fact.target_id].name,
            fact.description or "",
            fact.keywords or [],
        ]
    encoded = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def entity_vector_text(node: GraphNode, ontology: Ontology) -> str:
    """仅使用本体业务说明与当前来源事实，不引入其他 Chunk 的描述。"""
    return "\n".join(
        [
            f"ontology: {ontology.domain}",
            f"ontology_description: {ontology.description}",
            f"category: {node.category}",
            f"category_description: {ontology.entity_specs[node.category].description}",
            f"name: {node.name}",
            f"description: {node.description or ''}",
        ]
    )


def relationship_vector_text(
    edge: GraphEdge, nodes: dict[str, GraphNode], ontology: Ontology
) -> str:
    """有向两端、谓词说明与关系来源描述共同表达关系语义。"""
    source = nodes[edge.source_id]
    target = nodes[edge.target_id]
    return "\n".join(
        [
            f"ontology: {ontology.domain}",
            f"ontology_description: {ontology.description}",
            f"relation_type: {edge.relation_type}",
            f"relation_description: {ontology.relation_specs[edge.relation_type].description}",
            f"source: {source.name}",
            f"source_category: {source.category}",
            f"source_category_description: {ontology.entity_specs[source.category].description}",
            f"target: {target.name}",
            f"target_category: {target.category}",
            f"target_category_description: {ontology.entity_specs[target.category].description}",
            f"description: {edge.description or ''}",
            f"keywords: {', '.join(edge.keywords or [])}",
        ]
    )

"""Neo4j 中可由 Mongo 图谱事实重建的拓扑投影。"""

import asyncio
from collections.abc import Sequence
from typing import Any

from neo4j import AsyncDriver

from rag.application.graph.models import (
    GraphEdge,
    GraphEdgeProjection,
    GraphNode,
    GraphNodeProjection,
    graph_source_projection_id,
)
from rag.application.retrieval.models import TraversalDirection
from rag.domain.acl import PermissionScope, ResourceAcl
from rag.domain.repositories.graph_node_vectors import GraphVectorCandidate
from rag.domain.repositories.metadata_filters import (
    MetadataFilterCondition,
    MetadataFilterOperator,
)
from rag.domain.repositories.graph_topology import (
    GraphSourceProjection,
    GraphTopologyRepository,
)

# --- 常量与 Cypher 查询 ---

_NODE_LABEL = "RagV3GraphNode"
_EDGE_LABEL = "RagV3GraphEdge"
_SOURCE_LABEL = "RagV3GraphSource"

_SCHEMA = (
    f"CREATE CONSTRAINT rag_graph_node_id IF NOT EXISTS FOR (node:{_NODE_LABEL}) REQUIRE node.node_id IS UNIQUE",
    f"CREATE CONSTRAINT rag_graph_edge_id IF NOT EXISTS FOR (edge:{_EDGE_LABEL}) REQUIRE edge.edge_id IS UNIQUE",
    f"CREATE CONSTRAINT rag_graph_source_id IF NOT EXISTS FOR (source:{_SOURCE_LABEL}) REQUIRE source.projection_id IS UNIQUE",
    f"CREATE INDEX rag_graph_source_revision IF NOT EXISTS FOR (source:{_SOURCE_LABEL}) ON (source.resource_id, source.content_revision)",
)

# 删除一个 revision 的所有来源投影
_DELETE_REVISION = f"""
MATCH (source:{_SOURCE_LABEL} {{resource_id: $resource_id, content_revision: $content_revision}})
DETACH DELETE source
"""

# 清理无来源投影关联的逻辑节点和边（孤儿）
_DELETE_ORPHANS = f"""
MATCH (edge:{_EDGE_LABEL})
WHERE NOT (edge)<-[:RAG_V3_PROJECTS_EDGE]-()
DETACH DELETE edge
WITH 1 AS ignored
MATCH (node:{_NODE_LABEL})
WHERE NOT (node)<-[:RAG_V3_PROJECTS_NODE]-()
  AND NOT ()-[:RAG_V3_FROM]->(node)
  AND NOT ()-[:RAG_V3_TO]->(node)
DELETE node
"""

# 创建节点及来源投影，并关联
_UPSERT_NODES = f"""
UNWIND $items AS item
MERGE (node:{_NODE_LABEL} {{node_id: item.node_id}})
SET node.name = item.name,
    node.node_type = item.node_type,
    node.category = item.category,
    node.description = item.description,
    node.aliases = item.aliases,
    node.extra_meta = item.extra_meta
MERGE (source:{_SOURCE_LABEL} {{projection_id: item.projection_id}})
SET source.target_type = 'node',
    source.target_id = item.node_id,
    source.resource_id = item.resource_id,
    source.content_revision = item.content_revision,
    source.producer_id = item.producer_id,
    source.evidence_ids = item.evidence_ids,
    source.acl_revision = item.acl_revision,
    source.owner_id = item.owner_id,
    source.readable_users = item.readable_users,
    source.excluded_read_users = item.excluded_read_users,
    source.group_ids = item.group_ids,
    source.default_readable_group_ids = item.default_readable_group_ids,
    source.group_readable_subjects = item.group_readable_subjects,
    source.group_excluded_subjects = item.group_excluded_subjects
SET source += item.filter_properties
MERGE (source)-[:RAG_V3_PROJECTS_NODE]->(node)
"""

# 创建边及来源投影，并关联两端节点
_UPSERT_EDGES = f"""
UNWIND $items AS item
MATCH (source_node:{_NODE_LABEL} {{node_id: item.source_node_id}})
MATCH (target_node:{_NODE_LABEL} {{node_id: item.target_node_id}})
MERGE (edge:{_EDGE_LABEL} {{edge_id: item.edge_id}})
SET edge.relation_type = item.relation_type,
    edge.description = item.description,
    edge.keywords = item.keywords,
    edge.extra_meta = item.extra_meta
MERGE (edge)-[:RAG_V3_FROM]->(source_node)
MERGE (edge)-[:RAG_V3_TO]->(target_node)
MERGE (source:{_SOURCE_LABEL} {{projection_id: item.projection_id}})
SET source.target_type = 'edge',
    source.target_id = item.edge_id,
    source.resource_id = item.resource_id,
    source.content_revision = item.content_revision,
    source.producer_id = item.producer_id,
    source.evidence_ids = item.evidence_ids,
    source.acl_revision = item.acl_revision,
    source.owner_id = item.owner_id,
    source.readable_users = item.readable_users,
    source.excluded_read_users = item.excluded_read_users,
    source.group_ids = item.group_ids,
    source.default_readable_group_ids = item.default_readable_group_ids,
    source.group_readable_subjects = item.group_readable_subjects,
    source.group_excluded_subjects = item.group_excluded_subjects
SET source += item.filter_properties
MERGE (source)-[:RAG_V3_PROJECTS_EDGE]->(edge)
"""

# 删除多个资源的所有来源投影
_DELETE_RESOURCES = f"""
MATCH (source:{_SOURCE_LABEL})
WHERE source.resource_id IN $resource_ids
DETACH DELETE source
"""


# --- 仓储类 ---

class Neo4jGraphTopologyRepository(GraphTopologyRepository):
    """按来源投影保存图谱拓扑，逻辑图元不会被单一资源 revision 覆盖。"""

    def __init__(self, *, driver: AsyncDriver) -> None:
        self._driver = driver
        self._schema_lock = asyncio.Lock()
        self._schema_ready = False

    async def replace_revision(
        self,
        *,
        resource_id: str,
        content_revision: str,
        nodes: Sequence[GraphNodeProjection],
        edges: Sequence[GraphEdgeProjection],
        resource_acl: ResourceAcl,
    ) -> None:
        """原子替换一个 revision 的来源投影，清理孤儿逻辑图元。"""
        await self._ensure_schema()
        async with self._driver.session() as session:
            # 1. 删除该 revision 的所有来源投影
            await session.run(
                _DELETE_REVISION,
                resource_id=resource_id,
                content_revision=content_revision,
            )
            # 2. 写入节点来源
            node_items = [_node_item(item, resource_acl) for item in nodes]
            if node_items:
                await session.run(_UPSERT_NODES, items=node_items)
            # 3. 写入边来源
            edge_items = [_edge_item(item, resource_acl) for item in edges]
            if edge_items:
                await session.run(_UPSERT_EDGES, items=edge_items)
            # 4. 清理无来源的孤立逻辑节点/边
            await session.run(_DELETE_ORPHANS)

    async def delete_resources(self, resource_ids: Sequence[str]) -> None:
        """删除多个资源的所有来源投影。"""
        ids = list(dict.fromkeys(resource_ids))
        if not ids:
            return
        await self._ensure_schema()
        async with self._driver.session() as session:
            await session.run(_DELETE_RESOURCES, resource_ids=ids)
            await session.run(_DELETE_ORPHANS)

    async def traverse(
        self,
        *,
        candidates: Sequence[GraphVectorCandidate],
        seed_node_ids: Sequence[str],
        scope: PermissionScope,
        resource_ids: Sequence[str] | None,
        relation_types: Sequence[str],
        direction: TraversalDirection,
        max_depth: int,
        metadata_filters: Sequence[MetadataFilterCondition],
        limit: int,
    ) -> list[GraphSourceProjection]:
        """从向量候选和种子节点出发，按方向和关系类型扩展至多 max_depth 跳。"""
        if limit <= 0:
            return []
        await self._ensure_schema()

        # 构建 ACL 和过滤条件
        predicate, parameters = _source_predicate(
            scope=scope,
            resource_ids=resource_ids,
            metadata_filters=metadata_filters,
        )
        orders = {item.projection_id: index for index, item in enumerate(candidates)}
        projection_ids = list(orders)
        source_node_ids = list(dict.fromkeys(seed_node_ids))

        async with self._driver.session() as session:
            # 第一跳：直接从来源投影命中节点/边
            direct = await session.run(
                f"""
                MATCH (source:{_SOURCE_LABEL})-[projection:RAG_V3_PROJECTS_NODE|RAG_V3_PROJECTS_EDGE]->(target)
                WHERE (source.projection_id IN $projection_ids
                       OR (source.target_type = 'node' AND source.target_id IN $seed_node_ids))
                  AND {predicate}
                OPTIONAL MATCH (target)-[:RAG_V3_FROM]->(source_node:{_NODE_LABEL})
                OPTIONAL MATCH (target)-[:RAG_V3_TO]->(target_node:{_NODE_LABEL})
                RETURN source, target, source_node.node_id AS source_node_id,
                       target_node.node_id AS target_node_id,
                       source_node.name AS source_node_name,
                       target_node.name AS target_node_name
                ORDER BY CASE
                           WHEN source.target_type = 'node'
                                AND source.target_id IN $seed_node_ids THEN -1
                           ELSE indexOf($projection_ids, source.projection_id)
                         END,
                         source.projection_id
                LIMIT $limit
                """,
                projection_ids=projection_ids,
                seed_node_ids=source_node_ids,
                limit=limit,
                **parameters,
            )
            result = [
                _source_from_record(record, orders, 0) async for record in direct
            ]
            result.sort(key=lambda item: item.graph_rank)
            if max_depth == 0:
                return _deduplicate_sources(result)

            # 迭代扩展
            frontier = {item.target_id for item in result if item.target_type == "node"} | set(source_node_ids)
            for hop in range(1, max_depth + 1):
                if not frontier or len(result) >= limit:
                    break
                expanded = await session.run(
                    f"""
                    MATCH (edge:{_EDGE_LABEL})-[:RAG_V3_FROM]->(source_node:{_NODE_LABEL})
                    MATCH (edge)-[:RAG_V3_TO]->(target_node:{_NODE_LABEL})
                    WHERE (size($relation_types) = 0 OR edge.relation_type IN $relation_types)
                      AND {_direction_predicate(direction)}
                    MATCH (source:{_SOURCE_LABEL})-[:RAG_V3_PROJECTS_EDGE]->(edge)
                    WHERE {predicate}
                    RETURN source, edge AS target,
                           source_node.node_id AS source_node_id,
                           target_node.node_id AS target_node_id,
                           source_node.name AS source_node_name,
                           target_node.name AS target_node_name
                    LIMIT $limit
                    """,
                    frontier_node_ids=list(frontier),
                    relation_types=list(relation_types),
                    limit=limit - len(result),
                    **parameters,
                )
                edges = [
                    _source_from_record(record, orders, hop) async for record in expanded
                ]
                result.extend(edges)
                # 更新前沿：新发现的节点 ID
                frontier = {
                    node_id
                    for record in edges
                    if record.edge is not None
                    for node_id in (record.edge.source_node_id, record.edge.target_node_id)
                } - frontier
            return _deduplicate_sources(result)

    async def _ensure_schema(self) -> None:
        """确保 Neo4j 约束和索引存在（线程安全）。"""
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            async with self._driver.session() as session:
                for query in _SCHEMA:
                    await session.run(query)
            self._schema_ready = True


# --- 辅助函数：构造节点/边导入项 ---

def _node_item(item: GraphNodeProjection, acl: ResourceAcl) -> dict[str, Any]:
    """将节点投影转换为 Cypher 导入项。"""
    return {
        "projection_id": graph_source_projection_id(
            target_type="node",
            target_id=item.node.node_id,
            resource_id=item.resource_id,
            content_revision=item.content_revision,
            evidence_ids=item.evidence_ids,
            producer_id=item.producer_id,
        ),
        "node_id": item.node.node_id,
        "name": item.node.name,
        "node_type": item.node.node_type.value,
        "category": item.node.category,
        "description": item.node.description,
        "aliases": item.node.aliases,
        "extra_meta": item.node.extra_meta,
        **_source_properties(item, acl),
    }


def _edge_item(item: GraphEdgeProjection, acl: ResourceAcl) -> dict[str, Any]:
    """将边投影转换为 Cypher 导入项。"""
    return {
        "projection_id": graph_source_projection_id(
            target_type="edge",
            target_id=item.edge.edge_id,
            resource_id=item.resource_id,
            content_revision=item.content_revision,
            evidence_ids=item.evidence_ids,
            producer_id=item.producer_id,
        ),
        "edge_id": item.edge.edge_id,
        "source_node_id": item.edge.source_node_id,
        "target_node_id": item.edge.target_node_id,
        "relation_type": item.edge.relation_type,
        "description": item.edge.description,
        "keywords": item.edge.keywords,
        "extra_meta": item.edge.extra_meta,
        **_source_properties(item, acl),
    }


def _source_properties(
    item: GraphNodeProjection | GraphEdgeProjection,
    acl: ResourceAcl,
) -> dict[str, Any]:
    """提取来源投影的公共属性（包括 ACL 平铺和过滤字段前缀）。"""
    return {
        "resource_id": item.resource_id,
        "content_revision": item.content_revision,
        "producer_id": item.producer_id,
        "evidence_ids": item.evidence_ids,
        # 过滤字段加上前缀，以便 Cypher 中可以用 source['filter_xxx'] 匹配
        "filter_properties": {
            f"filter_{key}": value for key, value in item.filter_values.items()
        },
        "acl_revision": acl.acl_revision,
        "owner_id": acl.owner_id,
        "readable_users": list(acl.readable_users),
        "excluded_read_users": list(acl.excluded_read_users),
        "group_ids": [group.group_id for group in acl.group_acls],
        "default_readable_group_ids": [
            group.group_id for group in acl.group_acls if group.default_readable
        ],
        "group_readable_subjects": [
            _group_subject(group.group_id, user_id)
            for group in acl.group_acls
            for user_id in group.readable_users
        ],
        "group_excluded_subjects": [
            _group_subject(group.group_id, user_id)
            for group in acl.group_acls
            for user_id in group.excluded_read_users
        ],
    }


def _group_subject(group_id: str, user_id: str) -> str:
    """组合 group_id 和 user_id 为唯一主题字符串。"""
    return f"{group_id}\x1f{user_id}"


# --- 辅助函数：Cypher 条件构造 ---

def _source_predicate(
    *,
    scope: PermissionScope,
    resource_ids: Sequence[str] | None,
    metadata_filters: Sequence[MetadataFilterCondition],
) -> tuple[str, dict[str, Any]]:
    """构造来源节点的 ACL 和资源/元数据过滤条件。"""
    predicate = """
    (source.owner_id = $user_id
     OR $user_id IN source.readable_users
     OR (NOT $user_id IN source.excluded_read_users AND
         (any(group_id IN source.group_ids WHERE group_id IN $managed_group_ids)
          OR any(group_id IN source.default_readable_group_ids WHERE
                 group_id IN $joined_group_ids
                 AND NOT group_id + $subject_separator + $user_id IN source.group_excluded_subjects)
          OR any(group_id IN source.group_ids WHERE
                 group_id IN $joined_group_ids
                 AND group_id + $subject_separator + $user_id IN source.group_readable_subjects))))
    """
    parameters: dict[str, Any] = {
        "user_id": scope.user_id,
        "managed_group_ids": list(scope.managed_group_ids),
        "joined_group_ids": list(scope.joined_group_ids),
        "subject_separator": "\x1f",
    }
    if resource_ids:
        predicate += " AND source.resource_id IN $resource_ids"
        parameters["resource_ids"] = list(dict.fromkeys(resource_ids))
    # 处理元数据过滤条件
    for index, condition in enumerate(metadata_filters):
        parameter = f"metadata_filter_{index}"
        operator = {
            MetadataFilterOperator.EQ: "=",
            MetadataFilterOperator.GTE: ">=",
            MetadataFilterOperator.LTE: "<=",
        }[condition.operator]
        predicate += f" AND source[$metadata_field_{index}] {operator} ${parameter}"
        parameters[f"metadata_field_{index}"] = f"filter_{condition.field}"
        parameters[parameter] = condition.value
    return predicate, parameters


def _direction_predicate(direction: TraversalDirection) -> str:
    """根据方向返回边扩展条件。"""
    if direction is TraversalDirection.OUT:
        return "source_node.node_id IN $frontier_node_ids"
    if direction is TraversalDirection.IN:
        return "target_node.node_id IN $frontier_node_ids"
    return "source_node.node_id IN $frontier_node_ids OR target_node.node_id IN $frontier_node_ids"


# --- 辅助函数：记录转换与去重 ---

def _source_from_record(
    record: Any,
    orders: dict[str, int],
    hop_count: int,
) -> GraphSourceProjection:
    """将 Neo4j 记录转为 GraphSourceProjection。"""
    source = dict(record["source"])
    target = dict(record["target"])
    target_type = source["target_type"]
    node = None
    edge = None
    if target_type == "node":
        node = GraphNode(
            node_id=target["node_id"],
            name=target["name"],
            node_type=target["node_type"],
            category=target["category"],
            description=target.get("description", ""),
            aliases=tuple(target.get("aliases", ())),
            extra_meta=target.get("extra_meta", {}),
        )
    else:
        edge = GraphEdge(
            edge_id=target["edge_id"],
            source_node_id=record["source_node_id"],
            target_node_id=record["target_node_id"],
            relation_type=target["relation_type"],
            description=target.get("description", ""),
            keywords=tuple(target.get("keywords", ())),
            extra_meta=target.get("extra_meta", {}),
        )
    return GraphSourceProjection(
        projection_id=source["projection_id"],
        target_type=target_type,
        target_id=source["target_id"],
        resource_id=source["resource_id"],
        content_revision=source["content_revision"],
        evidence_ids=source.get("evidence_ids", []),
        producer_id=source.get("producer_id"),
        node=node,
        edge=edge,
        source_node_name=record.get("source_node_name") or "",
        target_node_name=record.get("target_node_name") or "",
        graph_rank=orders.get(source["projection_id"], 10_000),
        hop_count=hop_count,
    )


def _deduplicate_sources(
    sources: Sequence[GraphSourceProjection],
) -> list[GraphSourceProjection]:
    """按 projection_id 去重，保留最小 graph_rank。"""
    by_projection: dict[str, GraphSourceProjection] = {}
    for source in sources:
        current = by_projection.get(source.projection_id)
        if current is None or source.graph_rank < current.graph_rank:
            by_projection[source.projection_id] = source
    return list(by_projection.values())

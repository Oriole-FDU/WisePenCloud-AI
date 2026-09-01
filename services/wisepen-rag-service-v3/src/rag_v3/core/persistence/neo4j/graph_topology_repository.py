"""Neo4j 中可由 Mongo 图谱事实重建的拓扑投影。"""

import asyncio
from collections.abc import Sequence
from typing import Any

from neo4j import AsyncDriver

from rag_v3.domain.acl import PermissionScope, ResourceAcl
from rag_v3.domain.graph import (
    GraphEdge,
    GraphEdgeProjection,
    GraphFilterCondition,
    GraphFilterOperator,
    GraphNode,
    GraphNodeProjection,
    GraphSourceProjection,
    GraphVectorCandidate,
    TraversalDirection,
    graph_source_projection_id,
)
from rag_v3.domain.repositories.graph_projections import GraphTopologyRepository

_NODE_LABEL = "RagV3GraphNode"
_EDGE_LABEL = "RagV3GraphEdge"
_SOURCE_LABEL = "RagV3GraphSource"

_SCHEMA = (
    f"CREATE CONSTRAINT rag_v3_graph_node_id IF NOT EXISTS FOR (node:{_NODE_LABEL}) REQUIRE node.node_id IS UNIQUE",
    f"CREATE CONSTRAINT rag_v3_graph_edge_id IF NOT EXISTS FOR (edge:{_EDGE_LABEL}) REQUIRE edge.edge_id IS UNIQUE",
    f"CREATE CONSTRAINT rag_v3_graph_source_id IF NOT EXISTS FOR (source:{_SOURCE_LABEL}) REQUIRE source.projection_id IS UNIQUE",
    f"CREATE INDEX rag_v3_graph_source_revision IF NOT EXISTS FOR (source:{_SOURCE_LABEL}) ON (source.resource_id, source.content_revision)",
)

_DELETE_REVISION = f"""
MATCH (source:{_SOURCE_LABEL} {{resource_id: $resource_id, content_revision: $content_revision}})
DETACH DELETE source
"""

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

_DELETE_RESOURCES = f"""
MATCH (source:{_SOURCE_LABEL})
WHERE source.resource_id IN $resource_ids
DETACH DELETE source
"""


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
        if any(item.resource_id != resource_id for item in (*nodes, *edges)):
            raise ValueError("graph projections must belong to resource_id")
        if any(item.content_revision != content_revision for item in (*nodes, *edges)):
            raise ValueError("graph projections must belong to content_revision")
        if resource_acl.resource_id != resource_id:
            raise ValueError("resource ACL must belong to projected graph facts")

        await self._ensure_schema()
        async with self._driver.session() as session:
            await session.run(
                _DELETE_REVISION,
                resource_id=resource_id,
                content_revision=content_revision,
            )
            node_items = [_node_item(item, resource_acl) for item in nodes]
            if node_items:
                await session.run(_UPSERT_NODES, items=node_items)
            edge_items = [_edge_item(item, resource_acl) for item in edges]
            if edge_items:
                await session.run(_UPSERT_EDGES, items=edge_items)
            await session.run(_DELETE_ORPHANS)

    async def delete_resources(self, resource_ids: Sequence[str]) -> None:
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
        metadata_filters: Sequence[GraphFilterCondition],
        limit: int,
    ) -> list[GraphSourceProjection]:
        """从有限 vector/seed 起点扩展至多两跳，不扫描全图。"""
        if limit <= 0:
            return []
        await self._ensure_schema()
        predicate, parameters = _source_predicate(
            scope=scope,
            resource_ids=resource_ids,
            metadata_filters=metadata_filters,
        )
        ranks = {item.projection_id: item.rank for item in candidates}
        projection_ids = list(ranks)
        source_node_ids = list(dict.fromkeys(seed_node_ids))
        async with self._driver.session() as session:
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
                LIMIT $limit
                """,
                projection_ids=projection_ids,
                seed_node_ids=source_node_ids,
                limit=limit,
                **parameters,
            )
            result = [_source_from_record(record, ranks, 0) async for record in direct]
            if max_depth == 0:
                return _deduplicate_sources(result)

            frontier = {
                item.target_id
                for item in result
                if item.target_type == "node"
            } | set(source_node_ids)
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
                edges = [_source_from_record(record, ranks, hop) async for record in expanded]
                result.extend(edges)
                frontier = {
                    node_id
                    for record in edges
                    if record.edge is not None
                    for node_id in (record.edge.source_node_id, record.edge.target_node_id)
                } - frontier
            return _deduplicate_sources(result)

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            async with self._driver.session() as session:
                for query in _SCHEMA:
                    await session.run(query)
            self._schema_ready = True


def _node_item(item: GraphNodeProjection, acl: ResourceAcl) -> dict[str, Any]:
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
        "aliases": list(item.node.aliases),
        "extra_meta": item.node.extra_meta,
        **_source_properties(item, acl),
    }


def _edge_item(item: GraphEdgeProjection, acl: ResourceAcl) -> dict[str, Any]:
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
        "keywords": list(item.edge.keywords),
        "extra_meta": item.edge.extra_meta,
        **_source_properties(item, acl),
    }


def _source_properties(
    item: GraphNodeProjection | GraphEdgeProjection,
    acl: ResourceAcl,
) -> dict[str, Any]:
    return {
        "resource_id": item.resource_id,
        "content_revision": item.content_revision,
        "producer_id": item.producer_id,
        "evidence_ids": list(item.evidence_ids),
        # Neo4j property values cannot be maps. 过滤字段在来源节点上按前缀展开，
        # 这样插件编译出的条件才能与 Qdrant 一样下推。
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
    return f"{group_id}\x1f{user_id}"


def _source_predicate(
    *,
    scope: PermissionScope,
    resource_ids: Sequence[str] | None,
    metadata_filters: Sequence[GraphFilterCondition],
) -> tuple[str, dict[str, Any]]:
    """Neo4j 只做与 Qdrant 同语义的预过滤，授权结论仍由 Mongo 给出。"""
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
    for index, condition in enumerate(metadata_filters):
        parameter = f"metadata_filter_{index}"
        operator = {
            GraphFilterOperator.EQ: "=",
            GraphFilterOperator.GTE: ">=",
            GraphFilterOperator.LTE: "<=",
        }[condition.operator]
        predicate += f" AND source[$metadata_field_{index}] {operator} ${parameter}"
        parameters[f"metadata_field_{index}"] = f"filter_{condition.field}"
        parameters[parameter] = condition.value
    return predicate, parameters


def _direction_predicate(direction: TraversalDirection) -> str:
    if direction is TraversalDirection.OUT:
        return "source_node.node_id IN $frontier_node_ids"
    if direction is TraversalDirection.IN:
        return "target_node.node_id IN $frontier_node_ids"
    return "source_node.node_id IN $frontier_node_ids OR target_node.node_id IN $frontier_node_ids"


def _source_from_record(
    record: Any,
    ranks: dict[str, int],
    hop_count: int,
) -> GraphSourceProjection:
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
        evidence_ids=tuple(source.get("evidence_ids", ())),
        producer_id=source.get("producer_id"),
        node=node,
        edge=edge,
        source_node_name=record.get("source_node_name") or "",
        target_node_name=record.get("target_node_name") or "",
        graph_rank=ranks.get(source["projection_id"], 10_000),
        hop_count=hop_count,
    )


def _deduplicate_sources(
    sources: Sequence[GraphSourceProjection],
) -> list[GraphSourceProjection]:
    by_projection: dict[str, GraphSourceProjection] = {}
    for source in sources:
        current = by_projection.get(source.projection_id)
        if current is None or (source.graph_rank, source.hop_count) < (
            current.graph_rank,
            current.hop_count,
        ):
            by_projection[source.projection_id] = source
    return sorted(
        by_projection.values(),
        key=lambda item: (item.graph_rank, item.hop_count, item.target_id),
    )

"""Neo4j 中可由 Mongo 图谱事实重建的拓扑投影。"""

import asyncio
from collections.abc import Sequence
from typing import Any

from neo4j import AsyncDriver

from rag_v3.domain.acl import ResourceAcl
from rag_v3.domain.graph import (
    GraphEdgeProjection,
    GraphNodeProjection,
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
    source.filter_values = item.filter_values,
    source.acl_revision = item.acl_revision,
    source.owner_id = item.owner_id,
    source.readable_users = item.readable_users,
    source.excluded_read_users = item.excluded_read_users,
    source.group_acls = item.group_acls
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
    source.filter_values = item.filter_values,
    source.acl_revision = item.acl_revision,
    source.owner_id = item.owner_id,
    source.readable_users = item.readable_users,
    source.excluded_read_users = item.excluded_read_users,
    source.group_acls = item.group_acls
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
        "filter_values": item.filter_values,
        "acl_revision": acl.acl_revision,
        "owner_id": acl.owner_id,
        "readable_users": list(acl.readable_users),
        "excluded_read_users": list(acl.excluded_read_users),
        "group_acls": [
            {
                "group_id": group.group_id,
                "default_readable": group.default_readable,
                "readable_users": list(group.readable_users),
                "excluded_read_users": list(group.excluded_read_users),
            }
            for group in acl.group_acls
        ],
    }

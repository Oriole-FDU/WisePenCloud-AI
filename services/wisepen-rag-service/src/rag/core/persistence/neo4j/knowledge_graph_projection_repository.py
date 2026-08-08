from __future__ import annotations

from neo4j import AsyncDriver

from rag.application.rag.acl import RagResourceAclProjection
from rag.application.rag.graph_extraction import KnowledgeNodeKind
from rag.application.rag.graph_projection.models import KnowledgeGraphProjection
from rag.application.rag.graph_projection.projector import resource_node_id
from rag.domain.repositories import (
    KnowledgeGraphProjectionRepository,
    KnowledgeGraphProjectionSupersededError,
    RagAclProjectionTarget,
)

_SCHEMA_QUERIES = (
    """
    CREATE CONSTRAINT knowledge_node_id IF NOT EXISTS
    FOR (node:KnowledgeNode) REQUIRE node.node_id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT resource_group_acl_id IF NOT EXISTS
    FOR (acl:ResourceGroupAcl) REQUIRE acl.acl_id IS UNIQUE
    """,
    """
    CREATE INDEX knowledge_relation_evidence_resource IF NOT EXISTS
    FOR ()-[relation:KNOWLEDGE_RELATION]-()
    ON (relation.evidence_resource_id)
    """,
    """
    CREATE INDEX knowledge_mention_resource IF NOT EXISTS
    FOR ()-[mention:MENTIONS]-()
    ON (mention.evidence_resource_id)
    """,
)

_UPSERT_ACL = """
MERGE (resource:KnowledgeNode:ResourceNode {node_id: $node_id})
WITH resource
WHERE resource.acl_revision IS NULL OR resource.acl_revision <= $acl_revision
SET resource.resource_id = $resource_id,
    resource.acl_revision = $acl_revision,
    resource.owner_id = $owner_id,
    resource.readable_users = $readable_users,
    resource.excluded_read_users = $excluded_read_users
WITH resource
OPTIONAL MATCH (resource)-[old_relation:HAS_GROUP_ACL]->(:ResourceGroupAcl)
DELETE old_relation
WITH resource
UNWIND $group_acls AS item
MERGE (acl:ResourceGroupAcl {acl_id: item.acl_id})
SET acl.resource_id = $resource_id,
    acl.group_id = item.group_id,
    acl.is_readable = item.is_readable,
    acl.readable_users = item.readable_users,
    acl.excluded_read_users = item.excluded_read_users
MERGE (resource)-[:HAS_GROUP_ACL]->(acl)
"""

_UPSERT_ENTITIES = """
UNWIND $nodes AS item
MERGE (node:KnowledgeNode:EntityNode {node_id: item.node_id})
SET node.label = item.label,
    node.entity_type = item.entity_type
REMOVE node.canonical_key, node.type_tags
"""

_UPSERT_EXTERNAL_SOURCES = """
UNWIND $nodes AS item
MERGE (node:KnowledgeNode:ExternalSourceNode {node_id: item.node_id})
SET node.label = item.label
REMOVE node.source_key
"""

_UPSERT_RELATIONS = """
UNWIND $edges AS item
MATCH (source:KnowledgeNode {node_id: item.source_node_id})
MATCH (target:KnowledgeNode {node_id: item.target_node_id})
MERGE (source)-[relation:KNOWLEDGE_RELATION {edge_id: item.edge_id}]->(target)
SET relation.relation_type = item.relation_type,
    relation.predicate = item.predicate,
    relation.origin = 'extracted',
    relation.evidence_resource_id = $resource_id,
    relation.evidence_quotes = item.evidence_quotes,
    relation.evidence_source_ref_ids = item.evidence_source_ref_ids,
    relation.source_content_revision = $content_revision,
    relation.relation_revision = $relation_revision
REMOVE relation.relation_profile,
       relation.evidence_ref_ids,
       relation.evidence_start_offsets,
       relation.evidence_end_offsets,
       relation.assertions,
       relation.extractor_version
"""

_UPSERT_MENTIONS = """
UNWIND $mentions AS item
MATCH (resource:ResourceNode {resource_id: $resource_id})
MATCH (target:KnowledgeNode {node_id: item.node_id})
MERGE (resource)-[mention:MENTIONS {mention_id: item.mention_id}]->(target)
SET mention.parent_id = item.parent_id,
    mention.source_ref_ids = item.source_ref_ids,
    mention.evidence_quote = item.evidence_quote,
    mention.evidence_resource_id = $resource_id,
    mention.source_content_revision = $content_revision,
    mention.relation_revision = $relation_revision
REMOVE mention.evidence_ref_id,
       mention.evidence_start_offset,
       mention.evidence_end_offset
"""

_APPLY_REVISION = """
MATCH (resource:ResourceNode {resource_id: $resource_id})
WHERE resource.content_projection_revision = $content_revision
SET resource.applied_relation_revision = $relation_revision
RETURN true AS revision_applied
"""

_CLEAN_OLD_RELATIONS = """
MATCH ()-[relation:KNOWLEDGE_RELATION]->()
WHERE relation.evidence_resource_id = $resource_id
  AND relation.relation_revision <> $relation_revision
DELETE relation
"""

_CLEAN_OLD_MENTIONS = """
MATCH (:ResourceNode {resource_id: $resource_id})-[mention:MENTIONS]->()
WHERE mention.relation_revision <> $relation_revision
DELETE mention
"""

_DELETE_RESOURCE_RELATIONS = """
MATCH ()-[relation:KNOWLEDGE_RELATION]->()
WHERE relation.evidence_resource_id IN $resource_ids
DELETE relation
"""

_DELETE_RESOURCE_NODES = """
MATCH (resource:ResourceNode)
WHERE resource.resource_id IN $resource_ids
DETACH DELETE resource
"""

_DELETE_RESOURCE_ACLS = """
MATCH (acl:ResourceGroupAcl)
WHERE acl.resource_id IN $resource_ids
DETACH DELETE acl
"""

_DELETE_ORPHAN_NODES = """
MATCH (node:KnowledgeNode)
WHERE NOT node:ResourceNode AND NOT (node)--()
DELETE node
"""


class Neo4jKnowledgeGraphProjectionRepository(
    KnowledgeGraphProjectionRepository,
    RagAclProjectionTarget,
):
    """Neo4j 知识图谱投影写入仓储，包含 ACL 投影同步与资源级图数据删除。"""

    __slots__ = (
        "_database",
        "_driver",
    )

    def __init__(
        self,
        *,
        driver: AsyncDriver,
        database: str,
    ) -> None:
        self._driver = driver
        self._database = database

    async def initialize(self) -> None:
        """创建图数据库所需的唯一性约束和索引。"""
        for query in _SCHEMA_QUERIES:
            await self._driver.execute_query(query, database_=self._database)

    async def is_projection_applied(
        self,
        *,
        resource_id: str,
        content_revision: str,
    ) -> bool:
        """检查指定资源的投影是否已成功应用（版本匹配且已标记 revision）。"""
        result = await self._driver.execute_query(
            """
            MATCH (resource:ResourceNode {resource_id: $resource_id})
            RETURN resource.content_projection_revision = $content_revision
               AND resource.applied_relation_revision IS NOT NULL AS applied
            """,
            resource_id=resource_id,
            content_revision=content_revision,
            database_=self._database,
        )
        return bool(result.records and result.records[0]["applied"])

    async def invalidate_projection(
        self,
        *,
        resource_id: str,
        content_revision: str,
    ) -> None:
        """使投影失效：设置 applied_relation_revision 为 null，触发下次重算。"""
        await self._driver.execute_query(
            """
            MERGE (resource:KnowledgeNode:ResourceNode {node_id: $node_id})
            SET resource.resource_id = $resource_id,
                resource.content_projection_revision = $content_revision,
                resource.applied_relation_revision = null
            """,
            node_id=resource_node_id(resource_id),
            resource_id=resource_id,
            content_revision=content_revision,
            database_=self._database,
        )

    async def update_acl_projection(
        self,
        projection: RagResourceAclProjection,
    ) -> None:
        """写入/更新资源节点的 ACL 权限投影，包括分组级别的可读性。"""
        await self._driver.execute_query(
            _UPSERT_ACL,
            node_id=resource_node_id(projection.resource_id),
            resource_id=projection.resource_id,
            acl_revision=projection.acl_revision,
            owner_id=projection.owner_id,
            readable_users=list(projection.readable_users),
            excluded_read_users=list(projection.excluded_read_users),
            group_acls=[
                {
                    "acl_id": f"{projection.resource_id}:{acl.group_id}",
                    "group_id": acl.group_id,
                    "is_readable": acl.is_readable,
                    "readable_users": list(acl.readable_users),
                    "excluded_read_users": list(acl.excluded_read_users),
                }
                for acl in projection.computed_group_acls
            ],
            database_=self._database,
        )

    async def delete_resources(self, resource_ids: tuple[str, ...]) -> None:
        """删除指定资源的所有图数据：关系、节点、ACL，并清理孤立节点。"""
        unique_resource_ids = tuple(dict.fromkeys(resource_ids))
        if not unique_resource_ids:
            return
        params = {
            "resource_ids": list(unique_resource_ids),
            "database_": self._database,
        }
        await self._driver.execute_query(_DELETE_RESOURCE_RELATIONS, **params)
        await self._driver.execute_query(_DELETE_RESOURCE_NODES, **params)
        await self._driver.execute_query(_DELETE_RESOURCE_ACLS, **params)
        await self._driver.execute_query(
            _DELETE_ORPHAN_NODES,
            database_=self._database,
        )

    async def apply_projection(
        self,
        *,
        projection: KnowledgeGraphProjection,
    ) -> None:
        """应用知识图谱投影，并在 content_revision 被覆盖时中止提交。"""
        common_params = {
            "resource_id": projection.resource_id,
            "content_revision": projection.content_revision,
            "relation_revision": projection.relation_revision,
            "database_": self._database,
        }
        await self._driver.execute_query(
            _UPSERT_ENTITIES,
            nodes=[
                {
                    "node_id": node.node_id,
                    "label": node.label,
                    "entity_type": node.entity_type.value,
                }
                for node in projection.nodes
                if node.kind is KnowledgeNodeKind.ENTITY
                and node.entity_type is not None
            ],
            **common_params,
        )
        await self._driver.execute_query(
            _UPSERT_EXTERNAL_SOURCES,
            nodes=[
                {
                    "node_id": node.node_id,
                    "label": node.label,
                }
                for node in projection.nodes
                if node.kind is KnowledgeNodeKind.EXTERNAL_SOURCE
            ],
            **common_params,
        )
        await self._driver.execute_query(
            _UPSERT_RELATIONS,
            edges=[
                {
                    "edge_id": edge.edge_id,
                    "source_node_id": edge.source_node_id,
                    "target_node_id": edge.target_node_id,
                    "relation_type": edge.relation_type.value,
                    "predicate": edge.predicate,
                    "evidence_quotes": list(edge.evidence_quotes),
                    "evidence_source_ref_ids": list(edge.evidence_source_ref_ids),
                }
                for edge in projection.edges
            ],
            **common_params,
        )
        await self._driver.execute_query(
            _UPSERT_MENTIONS,
            mentions=[
                {
                    "mention_id": mention.mention_id,
                    "node_id": mention.node_id,
                    "parent_id": mention.parent_id,
                    "source_ref_ids": list(mention.source_ref_ids),
                    "evidence_quote": mention.evidence_quote,
                }
                for mention in projection.mentions
            ],
            **common_params,
        )
        apply_result = await self._driver.execute_query(
            _APPLY_REVISION,
            **common_params,
        )
        if not apply_result.records:
            raise KnowledgeGraphProjectionSupersededError(
                f"content revision {projection.content_revision} was superseded"
            )
        await self._driver.execute_query(_CLEAN_OLD_RELATIONS, **common_params)
        await self._driver.execute_query(_CLEAN_OLD_MENTIONS, **common_params)

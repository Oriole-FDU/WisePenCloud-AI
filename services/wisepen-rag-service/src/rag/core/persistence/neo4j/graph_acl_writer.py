"""将统一 ResourceAcl 事实同步到 Neo4j ResourceNode。"""

from neo4j import AsyncDriver

from rag.application.rag.index.graph.candidate_merge import resource_node_id
from rag.domain.models.acl import ResourceAcl
from rag.domain.repositories.neo4j.graph_acl_writer import GraphAclWriter
from rag.domain.repositories.redis.graph_query_subgraph_cache import (
    GraphQuerySubgraphCache,
)

_SCHEMA_QUERIES = (
    """
    CREATE CONSTRAINT rag_v2_resource_group_acl_id IF NOT EXISTS
    FOR (acl:RagV2ResourceGroupAcl) REQUIRE acl.acl_id IS UNIQUE
    """,
)

_SYNCHRONIZE = """
MERGE (resource:RagV2Node:RagV2ResourceNode {node_id: $node_id})
WITH resource
WHERE resource.acl_revision IS NULL OR resource.acl_revision <= $acl_revision
SET resource.resource_id = $resource_id,
    resource.acl_revision = $acl_revision,
    resource.owner_id = $owner_id,
    resource.readable_users = $readable_users,
    resource.excluded_read_users = $excluded_read_users
WITH resource
OPTIONAL MATCH (resource)-[old:RAG_V2_HAS_GROUP_ACL]->(:RagV2ResourceGroupAcl)
DELETE old
WITH resource
UNWIND $group_acls AS item
MERGE (acl:RagV2ResourceGroupAcl {acl_id: item.acl_id})
SET acl.resource_id = $resource_id,
    acl.group_id = item.group_id,
    acl.is_readable = item.is_readable,
    acl.readable_users = item.readable_users,
    acl.excluded_read_users = item.excluded_read_users
MERGE (resource)-[:RAG_V2_HAS_GROUP_ACL]->(acl)
"""


class Neo4jGraphAclWriter(GraphAclWriter):
    """维护 v2 ResourceNode 的 ACL 属性和 group ACL 关系。"""

    def __init__(
        self,
        *,
        driver: AsyncDriver,
        database: str,
        subgraph_cache: GraphQuerySubgraphCache,
    ) -> None:
        self._driver = driver
        self._database = database
        self._subgraph_cache = subgraph_cache

    async def initialize(self) -> None:
        for query in _SCHEMA_QUERIES:
            await self._driver.execute_query(query, database_=self._database)

    async def synchronize(self, resource_acl: ResourceAcl) -> None:
        await self._driver.execute_query(
            _SYNCHRONIZE,
            node_id=resource_node_id(resource_acl.resource_id),
            resource_id=resource_acl.resource_id,
            acl_revision=resource_acl.acl_revision,
            owner_id=resource_acl.owner_id,
            readable_users=list(resource_acl.readable_users),
            excluded_read_users=list(resource_acl.excluded_read_users),
            group_acls=[
                {
                    "acl_id": f"{resource_acl.resource_id}:{group_acl.group_id}",
                    "group_id": group_acl.group_id,
                    "is_readable": group_acl.default_readable,
                    "readable_users": list(group_acl.readable_users),
                    "excluded_read_users": list(group_acl.excluded_read_users),
                }
                for group_acl in resource_acl.group_acls
            ],
            database_=self._database,
        )
        await self._subgraph_cache.bump_epoch()

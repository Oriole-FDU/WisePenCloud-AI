from __future__ import annotations

from neo4j import AsyncDriver, RoutingControl

from chat.application.rag.acl import RagPermissionAuthorizer, RagResourceAclProjection
from chat.application.rag.graph_extraction import KnowledgeNodeKind
from chat.application.rag.graph_projection import (
    KnowledgeGraphProjection,
    resource_node_id,
)
from chat.application.rag.knowledge_navigation import (
    KnowledgeGraphExpandRequest,
    KnowledgeMentionSource,
    KnowledgeNavigationEdge,
    KnowledgeNavigationNode,
    KnowledgeNavigationPath,
)
from chat.application.rag.repositories import (
    KnowledgeGraphNavigationRepository,
    KnowledgeGraphProjectionRepository,
    KnowledgeGraphProjectionSupersededError,
)
from chat.application.rag.graph_extraction import (
    KnowledgeEntityType,
    KnowledgeRelationProfile,
    KnowledgeRelationType,
)
from chat.application.rag.retrieval import (
    RagPermissionFilterBuilder,
    RagPermissionScope,
)

# ── 图数据库 Schema 初始化 ──────────────────────────────────────────

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

# ── Cypher 查询模板 ─────────────────────────────────────────────────
# ACL 投影：写入资源节点的权限信息及其分组 ACL

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

# 实体节点：写入/更新知识实体

_UPSERT_ENTITIES = """
UNWIND $nodes AS item
MERGE (node:KnowledgeNode:EntityNode {node_id: item.node_id})
SET node.canonical_key = item.canonical_key,
    node.label = item.label,
    node.entity_type = item.entity_type,
    node.type_tags = item.type_tags
"""

# 外部来源节点：写入/更新外部知识来源

_UPSERT_EXTERNAL_SOURCES = """
UNWIND $nodes AS item
MERGE (node:KnowledgeNode:ExternalSourceNode {node_id: item.node_id})
SET node.source_key = item.source_key,
    node.label = item.label
"""

# 知识关系边：写入/更新实体间关系

_UPSERT_RELATIONS = """
UNWIND $edges AS item
MATCH (source:KnowledgeNode {node_id: item.source_node_id})
MATCH (target:KnowledgeNode {node_id: item.target_node_id})
MERGE (source)-[relation:KNOWLEDGE_RELATION {edge_id: item.edge_id}]->(target)
SET relation.relation_type = item.relation_type,
    relation.relation_profile = item.relation_profile,
    relation.predicate = item.predicate,
    relation.origin = 'extracted',
    relation.evidence_resource_id = $resource_id,
    relation.evidence_ref_ids = item.evidence_ref_ids,
    relation.evidence_source_ref_ids = item.evidence_source_ref_ids,
    relation.evidence_start_offsets = item.evidence_start_offsets,
    relation.evidence_end_offsets = item.evidence_end_offsets,
    relation.assertions = item.assertions,
    relation.extractor_version = $extractor_version,
    relation.source_content_revision = $content_revision,
    relation.relation_revision = $relation_revision
"""

# 提及关系边：写入资源对知识节点的 MENTIONS 关系

_UPSERT_MENTIONS = """
UNWIND $mentions AS item
MATCH (resource:ResourceNode {resource_id: $resource_id})
MATCH (target:KnowledgeNode {node_id: item.node_id})
MERGE (resource)-[mention:MENTIONS {mention_id: item.mention_id}]->(target)
SET mention.chunk_id = item.chunk_id,
    mention.source_ref_id = item.source_ref_id,
    mention.evidence_ref_id = item.evidence_ref_id,
    mention.evidence_start_offset = item.start_offset,
    mention.evidence_end_offset = item.end_offset,
    mention.evidence_resource_id = $resource_id,
    mention.source_content_revision = $content_revision,
    mention.relation_revision = $relation_revision
"""

# 投影版本标记：将 relation_revision 写入资源节点，标记投影已应用

_APPLY_REVISION = """
MATCH (resource:ResourceNode {resource_id: $resource_id})
WHERE resource.content_projection_revision = $content_revision
SET resource.applied_relation_revision = $relation_revision
RETURN true AS revision_applied
"""

# 清理旧版本数据：删除不属于当前 revision 的关系边和提及边

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

# 资源删除：按 resource_ids 删除关联的关系、节点、ACL 及孤立节点

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

# 孤立节点清理：删除没有连接任何边的非资源节点

_DELETE_ORPHAN_NODES = """
MATCH (node:KnowledgeNode)
WHERE NOT node:ResourceNode AND NOT (node)--()
DELETE node
"""


# ── 图导航路径匹配模式 ──────────────────────────────────────────────
# 按方向 (out/in/both) × 深度 (1/2) 组织的 Cypher 路径模式

_PATH_PATTERNS = {
    ("out", 1): "(seed)-[:KNOWLEDGE_RELATION|MENTIONS*1]->(target)",
    ("out", 2): "(seed)-[:KNOWLEDGE_RELATION|MENTIONS*1..2]->(target)",
    ("in", 1): "(seed)<-[:KNOWLEDGE_RELATION|MENTIONS*1]-(target)",
    ("in", 2): "(seed)<-[:KNOWLEDGE_RELATION|MENTIONS*1..2]-(target)",
    ("both", 1): "(seed)-[:KNOWLEDGE_RELATION|MENTIONS*1]-(target)",
    ("both", 2): "(seed)-[:KNOWLEDGE_RELATION|MENTIONS*1..2]-(target)",
}


# ── 仓储实现 ─────────────────────────────────────────────────────────


class Neo4jKnowledgeGraphRepository(
    KnowledgeGraphProjectionRepository,
    KnowledgeGraphNavigationRepository,
):
    __slots__ = (
        "_database",
        "_driver",
        "_permission_authorizer",
        "_permission_filter_builder",
    )

    def __init__(
        self,
        *,
        driver: AsyncDriver,
        database: str,
        permission_authorizer: RagPermissionAuthorizer,
        permission_filter_builder: RagPermissionFilterBuilder,
    ) -> None:
        self._driver = driver
        self._database = database
        self._permission_authorizer = permission_authorizer
        self._permission_filter_builder = permission_filter_builder

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
        """应用知识图谱投影：写入实体/外部来源节点、关系边和提及边，标记版本并清理旧数据。

        若 content_revision 已被更新的投影覆盖，抛出 KnowledgeGraphProjectionSupersededError。
        """
        common_params = {
            "resource_id": projection.resource_id,
            "content_revision": projection.content_revision,
            "relation_revision": projection.relation_revision,
            "extractor_version": projection.extractor_version,
            "database_": self._database,
        }
        # 1. 写入实体节点
        await self._driver.execute_query(
            _UPSERT_ENTITIES,
            nodes=[
                {
                    "node_id": node.node_id,
                    "canonical_key": node.canonical_key,
                    "label": node.label,
                    "entity_type": node.entity_type.value,
                    "type_tags": [node.entity_type.value],
                }
                for node in projection.nodes
                if node.kind is KnowledgeNodeKind.ENTITY
                and node.entity_type is not None
            ],
            **common_params,
        )
        # 2. 写入外部来源节点
        await self._driver.execute_query(
            _UPSERT_EXTERNAL_SOURCES,
            nodes=[
                {
                    "node_id": node.node_id,
                    "source_key": node.source_key,
                    "label": node.label,
                }
                for node in projection.nodes
                if node.kind is KnowledgeNodeKind.EXTERNAL_SOURCE
            ],
            **common_params,
        )
        # 3. 写入知识关系边
        await self._driver.execute_query(
            _UPSERT_RELATIONS,
            edges=[
                {
                    "edge_id": edge.edge_id,
                    "source_node_id": edge.source_node_id,
                    "target_node_id": edge.target_node_id,
                    "relation_type": edge.relation_type.value,
                    "relation_profile": edge.relation_profile.value,
                    "predicate": edge.predicate,
                    "evidence_ref_ids": list(edge.evidence_ref_ids),
                    "evidence_source_ref_ids": list(edge.evidence_source_ref_ids),
                    "evidence_start_offsets": list(edge.evidence_start_offsets),
                    "evidence_end_offsets": list(edge.evidence_end_offsets),
                    "assertions": [item.value for item in edge.assertions],
                }
                for edge in projection.edges
            ],
            **common_params,
        )
        # 4. 写入提及关系边
        await self._driver.execute_query(
            _UPSERT_MENTIONS,
            mentions=[
                {
                    "mention_id": mention.mention_id,
                    "node_id": mention.node_id,
                    "chunk_id": mention.chunk_id,
                    "source_ref_id": mention.source_ref_id,
                    "evidence_ref_id": mention.evidence_ref_id,
                    "start_offset": mention.start_offset,
                    "end_offset": mention.end_offset,
                }
                for mention in projection.mentions
            ],
            **common_params,
        )
        # 5. 标记版本号，若 revision 已被覆盖则抛异常中止
        apply_result = await self._driver.execute_query(
            _APPLY_REVISION,
            **common_params,
        )
        if not apply_result.records:
            raise KnowledgeGraphProjectionSupersededError(
                f"content revision {projection.content_revision} was superseded"
            )
        # 6. 清理旧版本的关系边和提及边
        await self._driver.execute_query(_CLEAN_OLD_RELATIONS, **common_params)
        await self._driver.execute_query(_CLEAN_OLD_MENTIONS, **common_params)

    async def resolve_mentions(
        self,
        *,
        sources: tuple[KnowledgeMentionSource, ...],
        permission_scope: RagPermissionScope,
        limit: int = 32,
    ) -> tuple[KnowledgeNavigationNode, ...]:
        """根据 MENTIONS 关系解析指定资源中提及的知识节点，受权限过滤。"""
        if not sources or limit <= 0:
            return ()
        # 预过滤：仅保留当前用户有权限访问的资源
        accessible_resource_ids = (
            await self._permission_authorizer.accessible_resource_ids(
                (source.resource_id for source in sources),
                permission_scope,
            )
        )
        sources = tuple(
            source
            for source in sources
            if source.resource_id in accessible_resource_ids
        )
        if not sources:
            return ()
        # 构建 Cypher 权限谓词，注入到查询的 WHERE 子句中
        acl_predicate, acl_params = (
            self._permission_filter_builder.build_neo4j_predicate(
                permission_scope,
                node_alias="resource",
            )
        )
        result = await self._driver.execute_query(
            f"""
            UNWIND $sources AS item
            MATCH (resource:ResourceNode {{resource_id: item.resource_id}})
                  -[mention:MENTIONS]->(node:KnowledgeNode)
            WHERE mention.chunk_id = item.chunk_id
              AND resource.content_projection_revision = mention.source_content_revision
              AND resource.applied_relation_revision = mention.relation_revision
              AND {acl_predicate}
            RETURN DISTINCT node.node_id AS node_id,
                   CASE
                     WHEN node:EntityNode THEN 'Entity'
                     WHEN node:ExternalSourceNode THEN 'ExternalSource'
                     ELSE 'Resource'
                   END AS kind,
                   coalesce(node.label, node.resource_id) AS label,
                   node.entity_type AS entity_type,
                   coalesce(node.type_tags, []) AS type_tags
            ORDER BY node_id
            LIMIT $limit
            """,
            sources=[
                {"resource_id": item.resource_id, "chunk_id": item.chunk_id}
                for item in dict.fromkeys(sources)
            ],
            limit=limit,
            **acl_params,
            database_=self._database,
            routing_=RoutingControl.READ,
        )
        return tuple(
            _map_node(
                {
                    "node_id": record["node_id"],
                    "kind": record["kind"],
                    "label": record["label"],
                    "entity_type": record["entity_type"],
                    "type_tags": record["type_tags"],
                }
            )
            for record in result.records
        )

    async def expand(
        self,
        request: KnowledgeGraphExpandRequest,
    ) -> tuple[KnowledgeNavigationPath, ...]:
        """从种子节点出发沿 KNOWLEDGE_RELATION/MENTIONS 边展开导航路径，受权限过滤。"""
        if (
            not request.seed_node_ids
            or request.limit <= 0
            or request.max_depth not in (1, 2)
        ):
            return ()
        pattern = _PATH_PATTERNS[(request.direction.value, request.max_depth)]
        # 构建权限谓词：evidence_acl 过滤关系来源资源的可读性，endpoint_acl 过滤路径端点节点的可读性
        evidence_acl, acl_params = (
            self._permission_filter_builder.build_neo4j_predicate(
                request.permission_scope,
                node_alias="evidence",
            )
        )
        endpoint_acl, _ = self._permission_filter_builder.build_neo4j_predicate(
            request.permission_scope,
            node_alias="path_node",
        )
        result = await self._driver.execute_query(
            f"""
            MATCH (seed:KnowledgeNode)
            WHERE seed.node_id IN $seed_node_ids
            MATCH path={pattern}
            WHERE target <> seed
              AND NOT target.node_id IN $known_node_ids
              AND all(path_node IN nodes(path)
                WHERE NOT path_node:ResourceNode OR {endpoint_acl})
              AND all(relation IN relationships(path)
                WHERE (
                  size($relation_types) = 0
                  OR coalesce(relation.relation_type, type(relation))
                     IN $relation_types
                )
                AND EXISTS {{
                  MATCH (evidence:ResourceNode {{
                    resource_id: relation.evidence_resource_id
                  }})
                  WHERE evidence.content_projection_revision =
                        relation.source_content_revision
                    AND evidence.applied_relation_revision =
                        relation.relation_revision
                    AND {evidence_acl}
                }})
              AND all(path_node IN nodes(path)
                WHERE single(other IN nodes(path) WHERE other = path_node))
            RETURN [path_node IN nodes(path) | {{
                     node_id: path_node.node_id,
                     kind: CASE
                       WHEN path_node:EntityNode THEN 'Entity'
                       WHEN path_node:ExternalSourceNode THEN 'ExternalSource'
                       ELSE 'Resource'
                     END,
                     label: coalesce(path_node.label, path_node.resource_id),
                     entity_type: path_node.entity_type,
                     type_tags: coalesce(path_node.type_tags, [])
                   }}] AS nodes,
                   [relation IN relationships(path) | {{
                     edge_id: coalesce(relation.edge_id, relation.mention_id),
                     source_node_id: startNode(relation).node_id,
                     target_node_id: endNode(relation).node_id,
                     relation_type: coalesce(
                       relation.relation_type,
                       type(relation)
                     ),
                     relation_profile: coalesce(
                       relation.relation_profile,
                       'core'
                     ),
                     predicate: relation.predicate,
                     evidence_resource_id: relation.evidence_resource_id,
                     evidence_ref_ids: coalesce(
                       relation.evidence_ref_ids,
                       [relation.evidence_ref_id]
                     ),
                     evidence_source_ref_ids: coalesce(
                       relation.evidence_source_ref_ids,
                       [relation.source_ref_id]
                     ),
                     source_content_revision: relation.source_content_revision,
                     relation_revision: relation.relation_revision
                   }}] AS edges
            ORDER BY size(edges), nodes[-1].node_id
            LIMIT $limit
            """,
            seed_node_ids=list(dict.fromkeys(request.seed_node_ids)),
            known_node_ids=list(dict.fromkeys(request.known_node_ids)),
            relation_types=[item.value for item in request.relation_types],
            limit=request.limit,
            **acl_params,
            database_=self._database,
            routing_=RoutingControl.READ,
        )
        # 将 Cypher 返回的 nodes/edges 列表映射为领域对象
        paths = tuple(
            KnowledgeNavigationPath(
                nodes=tuple(_map_node(item) for item in record["nodes"]),
                edges=tuple(_map_edge(item) for item in record["edges"]),
            )
            for record in result.records
        )
        # 二次权限校验：确认路径中每条边的 evidence_resource_id 均对当前用户可读
        accessible_resource_ids = (
            await self._permission_authorizer.accessible_resource_ids(
                (edge.evidence_resource_id for path in paths for edge in path.edges),
                request.permission_scope,
            )
        )
        return tuple(
            path
            for path in paths
            if all(
                edge.evidence_resource_id in accessible_resource_ids
                for edge in path.edges
            )
        )


# ── 查询结果映射函数 ────────────────────────────────────────────────


def _map_node(item: dict) -> KnowledgeNavigationNode:
    """将 Cypher 查询返回的节点字典映射为 KnowledgeNavigationNode 领域对象。"""
    entity_type = item.get("entity_type")
    return KnowledgeNavigationNode(
        node_id=item["node_id"],
        kind=KnowledgeNodeKind(item["kind"]),
        label=item["label"],
        entity_type=(
            KnowledgeEntityType(entity_type) if entity_type is not None else None
        ),
        type_tags=tuple(item.get("type_tags") or ()),
    )


def _map_edge(item: dict) -> KnowledgeNavigationEdge:
    """将 Cypher 查询返回的边字典映射为 KnowledgeNavigationEdge 领域对象。"""
    return KnowledgeNavigationEdge(
        edge_id=item["edge_id"],
        source_node_id=item["source_node_id"],
        target_node_id=item["target_node_id"],
        relation_type=KnowledgeRelationType(item["relation_type"]),
        relation_profile=KnowledgeRelationProfile(item["relation_profile"]),
        predicate=item.get("predicate"),
        evidence_resource_id=item["evidence_resource_id"],
        evidence_ref_ids=tuple(
            value for value in item.get("evidence_ref_ids") or () if value
        ),
        evidence_source_ref_ids=tuple(
            value for value in item.get("evidence_source_ref_ids") or () if value
        ),
        source_content_revision=item["source_content_revision"],
        relation_revision=item["relation_revision"],
    )

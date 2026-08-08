from __future__ import annotations

from neo4j import AsyncDriver, RoutingControl

from rag.application.rag.acl import RagPermissionAuthorizer
from rag.application.rag.graph_extraction import (
    KnowledgeEntityType,
    KnowledgeNodeKind,
    KnowledgeRelationType,
)
from rag.application.rag.knowledge_navigation import (
    KnowledgeGraphCypherRequest,
    KnowledgeMentionSource,
    KnowledgeNavigationEdge,
    KnowledgeNavigationNode,
    KnowledgeNavigationPath,
)
from rag.application.rag.retrieval import (
    RagPermissionScope,
    build_neo4j_permission_predicate,
)
from rag.domain.repositories import KnowledgeGraphNavigationRepository

_PATH_PATTERNS = {
    ("out", 1): "(seed)-[:KNOWLEDGE_RELATION|MENTIONS*1]->(target)",
    ("out", 2): "(seed)-[:KNOWLEDGE_RELATION|MENTIONS*1..2]->(target)",
    ("in", 1): "(seed)<-[:KNOWLEDGE_RELATION|MENTIONS*1]-(target)",
    ("in", 2): "(seed)<-[:KNOWLEDGE_RELATION|MENTIONS*1..2]-(target)",
    ("both", 1): "(seed)-[:KNOWLEDGE_RELATION|MENTIONS*1]-(target)",
    ("both", 2): "(seed)-[:KNOWLEDGE_RELATION|MENTIONS*1..2]-(target)",
}


class Neo4jKnowledgeGraphNavigationRepository(KnowledgeGraphNavigationRepository):
    """Neo4j 知识图谱只读导航仓储，负责 ACL 约束下的 mention 解析和路径查询。"""

    __slots__ = (
        "_database",
        "_driver",
        "_permission_authorizer",
    )

    def __init__(
        self,
        *,
        driver: AsyncDriver,
        database: str,
        permission_authorizer: RagPermissionAuthorizer,
    ) -> None:
        self._driver = driver
        self._database = database
        self._permission_authorizer = permission_authorizer

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
        acl_predicate, acl_params = build_neo4j_permission_predicate(
            permission_scope,
            node_alias="resource",
        )
        result = await self._driver.execute_query(
            f"""
            UNWIND $sources AS item
            MATCH (resource:ResourceNode {{resource_id: item.resource_id}})
                  -[mention:MENTIONS]->(node:KnowledgeNode)
            WHERE item.source_ref_id IN mention.source_ref_ids
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
                   node.entity_type AS entity_type
            ORDER BY node_id
            LIMIT $limit
            """,
            sources=[
                {"resource_id": item.resource_id, "source_ref_id": item.source_ref_id}
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
                }
            )
            for record in result.records
        )

    async def cypher(
        self,
        request: KnowledgeGraphCypherRequest,
    ) -> tuple[KnowledgeNavigationPath, ...]:
        """从种子节点出发执行有界 Cypher 导航路径查询，受权限过滤。"""
        if (
            not request.seed_node_ids
            or request.limit <= 0
            or request.max_depth not in (1, 2)
        ):
            return ()
        pattern = _PATH_PATTERNS[(request.direction.value, request.max_depth)]
        evidence_acl, acl_params = build_neo4j_permission_predicate(
            request.permission_scope,
            node_alias="evidence",
        )
        endpoint_acl, _ = build_neo4j_permission_predicate(
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
                     entity_type: path_node.entity_type
                   }}] AS nodes,
                   [relation IN relationships(path) | {{
                     edge_id: coalesce(relation.edge_id, relation.mention_id),
                     source_node_id: startNode(relation).node_id,
                     target_node_id: endNode(relation).node_id,
                     relation_type: coalesce(
                       relation.relation_type,
                       type(relation)
                     ),
                     predicate: relation.predicate,
                     evidence_resource_id: relation.evidence_resource_id,
                     evidence_quotes: coalesce(
                       relation.evidence_quotes,
                       [relation.evidence_quote]
                      ),
                      evidence_source_ref_ids: coalesce(
                        relation.evidence_source_ref_ids,
                        relation.source_ref_ids
                      )
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
        paths = tuple(
            KnowledgeNavigationPath(
                nodes=tuple(_map_node(item) for item in record["nodes"]),
                edges=tuple(_map_edge(item) for item in record["edges"]),
            )
            for record in result.records
        )
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


def _map_node(item: dict) -> KnowledgeNavigationNode:
    entity_type = item.get("entity_type")
    return KnowledgeNavigationNode(
        node_id=item["node_id"],
        kind=KnowledgeNodeKind(item["kind"]),
        label=item["label"],
        entity_type=(
            KnowledgeEntityType(entity_type) if entity_type is not None else None
        ),
    )


def _map_edge(item: dict) -> KnowledgeNavigationEdge:
    return KnowledgeNavigationEdge(
        edge_id=item["edge_id"],
        source_node_id=item["source_node_id"],
        target_node_id=item["target_node_id"],
        relation_type=KnowledgeRelationType(item["relation_type"]),
        predicate=item.get("predicate"),
        evidence_resource_id=item["evidence_resource_id"],
        evidence_quotes=tuple(
            value for value in item.get("evidence_quotes") or () if value
        ),
        evidence_source_ref_ids=tuple(
            value for value in item.get("evidence_source_ref_ids") or () if value
        ),
    )

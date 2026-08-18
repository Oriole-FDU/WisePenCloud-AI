"""Neo4j v2 知识图谱发布与查询的统一 adapter。"""

from collections.abc import Sequence
from dataclasses import replace
from enum import StrEnum
from time import perf_counter

from common.logger import debug
from neo4j import AsyncDriver, RoutingControl

from rag.application.rag.index.graph.candidate_merge import resource_node_id
from rag.domain.models.acl import PermissionScope
from rag.domain.models.graph import (
    GraphEvidence,
    KnowledgeEntityType,
    KnowledgeGraph,
    KnowledgeMention,
    KnowledgeNode,
    KnowledgeNodeKind,
    KnowledgeRelationType,
    TraversalDirection,
)
from rag.domain.repositories.neo4j.knowledge_graph_repository import (
    GraphQuerySubgraph,
    GraphSeedBlock,
    KnowledgeGraphRepository,
    KnowledgeGraphRevisionSupersededError,
    TraversedEdge,
    TraversedPath,
)
from rag.domain.repositories.redis.graph_query_subgraph_cache import (
    GraphQuerySubgraphCache,
)
from common.utils.document import SourceSpan


class GraphStatus(StrEnum):
    """Neo4j 资源图节点的持久化发布状态。"""

    BUILDING = "building"
    PUBLISHED = "published"
    SKIPPED = "skipped"

_NODE_LABEL = "RagV2Node"
_RESOURCE_LABEL = "RagV2ResourceNode"
_ENTITY_LABEL = "RagV2EntityNode"
_EXTERNAL_SOURCE_LABEL = "RagV2ExternalSourceNode"
_RELATION_TYPE = "RAG_V2_KNOWLEDGE_RELATION"
_MENTION_TYPE = "RAG_V2_MENTION"

_SCHEMA_QUERIES = (
    """
    CREATE CONSTRAINT rag_v2_node_id IF NOT EXISTS
    FOR (node:RagV2Node) REQUIRE node.node_id IS UNIQUE
    """,
    """
    CREATE INDEX rag_v2_relation_resource IF NOT EXISTS
    FOR ()-[relation:RAG_V2_KNOWLEDGE_RELATION]-()
    ON (relation.evidence_resource_id)
    """,
    """
    CREATE INDEX rag_v2_mention_resource IF NOT EXISTS
    FOR ()-[mention:RAG_V2_MENTION]-()
    ON (mention.evidence_resource_id)
    """,
)

_BEGIN_BUILD = f"""
MERGE (resource:{_NODE_LABEL}:{_RESOURCE_LABEL} {{node_id: $node_id}})
ON CREATE SET resource.document_version = $document_version
WITH resource
WHERE resource.document_version IS NULL
   OR resource.document_version <= $document_version
SET resource.resource_id = $resource_id,
    resource.content_revision = $content_revision,
    resource.document_version = $document_version,
    resource.graph_status = '{GraphStatus.BUILDING.value}',
    resource.graph_revision = null
RETURN resource.resource_id AS resource_id
"""

_UPSERT_ENTITIES = f"""
UNWIND $nodes AS item
MERGE (node:{_NODE_LABEL}:{_ENTITY_LABEL} {{node_id: item.node_id}})
SET node.label = item.label,
    node.entity_type = item.entity_type
"""

_UPSERT_EXTERNAL_SOURCES = f"""
UNWIND $nodes AS item
MERGE (node:{_NODE_LABEL}:{_EXTERNAL_SOURCE_LABEL} {{node_id: item.node_id}})
SET node.label = item.label
"""

_UPSERT_RELATIONS = f"""
UNWIND $relations AS item
MATCH (source:{_NODE_LABEL} {{node_id: item.source_node_id}})
MATCH (target:{_NODE_LABEL} {{node_id: item.target_node_id}})
MERGE (source)-[relation:{_RELATION_TYPE} {{edge_id: item.edge_id}}]->(target)
SET relation.relation_type = item.relation_type,
    relation.predicate = item.predicate,
    relation.evidence_resource_id = $resource_id,
    relation.evidence_ids = item.evidence_ids,
    relation.evidence_resource_ids = item.evidence_resource_ids,
    relation.evidence_content_revisions = item.evidence_content_revisions,
    relation.evidence_reading_block_ids = item.evidence_reading_block_ids,
    relation.evidence_quotes = item.evidence_quotes,
    relation.evidence_start_offsets = item.evidence_start_offsets,
    relation.evidence_end_offsets = item.evidence_end_offsets,
    relation.source_content_revision = $content_revision,
    relation.graph_revision = $graph_revision
"""

_UPSERT_MENTIONS = f"""
UNWIND $mentions AS item
MATCH (resource:{_RESOURCE_LABEL} {{resource_id: $resource_id}})
MATCH (target:{_NODE_LABEL} {{node_id: item.node_id}})
MERGE (resource)-[mention:{_MENTION_TYPE} {{mention_id: item.mention_id}}]->(target)
SET mention.reading_block_id = item.reading_block_id,
    mention.evidence_id = item.evidence_id,
    mention.evidence_quote = item.evidence_quote,
    mention.evidence_start_offset = item.evidence_start_offset,
    mention.evidence_end_offset = item.evidence_end_offset,
    mention.evidence_resource_id = $resource_id,
    mention.source_content_revision = $content_revision,
    mention.graph_revision = $graph_revision
"""

_PUBLISH = f"""
MATCH (resource:{_RESOURCE_LABEL} {{resource_id: $resource_id}})
WHERE resource.document_version = $document_version
  AND resource.content_revision = $content_revision
  AND resource.graph_status = '{GraphStatus.BUILDING.value}'
SET resource.graph_status = '{GraphStatus.PUBLISHED.value}',
    resource.graph_revision = $graph_revision
RETURN resource.resource_id AS resource_id
"""

_SKIP = f"""
MATCH (resource:{_RESOURCE_LABEL} {{resource_id: $resource_id}})
WHERE resource.document_version = $document_version
  AND resource.content_revision = $content_revision
SET resource.graph_status = '{GraphStatus.SKIPPED.value}',
    resource.graph_revision = null
RETURN resource.resource_id AS resource_id
"""

_DELETE_OLD_RELATIONS = f"""
MATCH ()-[relation:{_RELATION_TYPE}]->()
WHERE relation.evidence_resource_id = $resource_id
  AND relation.graph_revision <> $graph_revision
DELETE relation
"""

_DELETE_OLD_MENTIONS = f"""
MATCH (resource:{_RESOURCE_LABEL} {{resource_id: $resource_id}})-[mention:{_MENTION_TYPE}]->()
WHERE mention.graph_revision <> $graph_revision
DELETE mention
"""

_DELETE_RESOURCES = f"""
MATCH ()-[relation:{_RELATION_TYPE}]->()
WHERE relation.evidence_resource_id IN $resource_ids
DELETE relation
WITH 1 AS ignored
MATCH (resource:{_RESOURCE_LABEL})
WHERE resource.resource_id IN $resource_ids
DETACH DELETE resource
"""

_DELETE_ORPHAN_NODES = f"""
MATCH (node:{_NODE_LABEL})
WHERE NOT node:{_RESOURCE_LABEL} AND NOT (node)--()
DELETE node
"""

_DELETE_ORPHAN_GROUP_ACLS = """
MATCH (acl:RagV2ResourceGroupAcl)
WHERE NOT ()-[:RAG_V2_HAS_GROUP_ACL]->(acl)
DELETE acl
"""

_FIND_NODES = """
UNWIND $reading_blocks AS item
MATCH (resource:RagV2ResourceNode {{resource_id: item.resource_id}})
      -[mention:RAG_V2_MENTION]->(node:RagV2Node)
WHERE resource.graph_status = $published_status
  AND resource.content_revision = item.content_revision
  AND mention.source_content_revision = item.content_revision
  AND mention.evidence_resource_id = resource.resource_id
  AND mention.graph_revision = resource.graph_revision
  AND mention.reading_block_id = item.reading_block_id
  AND {acl_filter}
WITH node,
     min(item.rank) AS block_rank,
     min(CASE
       WHEN any(span IN item.matched_source_spans
         WHERE span.start_offset < mention.evidence_end_offset
           AND span.end_offset > mention.evidence_start_offset)
       THEN 0 ELSE 1
     END) AS match_rank,
     min(mention.evidence_start_offset) AS evidence_start
RETURN node.node_id AS node_id,
       CASE
           WHEN node:RagV2EntityNode THEN 'Entity'
           WHEN node:RagV2ExternalSourceNode THEN 'ExternalSource'
           ELSE 'Resource'
       END AS kind,
       coalesce(node.label, node.resource_id) AS label,
       node.entity_type AS entity_type,
       node.resource_id AS resource_id
ORDER BY block_rank, match_rank, evidence_start, node_id
LIMIT $limit
"""

_FIND_MENTIONS = """
UNWIND $node_ids AS requested_node_id
MATCH (resource:RagV2ResourceNode)-[mention:RAG_V2_MENTION]->
      (node:RagV2Node)
WHERE node.node_id = requested_node_id
  AND resource.resource_id IN $resource_ids
  AND resource.graph_status = $published_status
  AND mention.source_content_revision = resource.content_revision
  AND mention.evidence_resource_id = resource.resource_id
  AND mention.graph_revision = resource.graph_revision
  AND {acl_filter}
WITH requested_node_id AS node_id, mention
ORDER BY node_id, mention.evidence_start_offset, mention.evidence_id
WITH node_id, mention.reading_block_id AS block_id, head(collect(mention)) AS mention
WITH node_id, mention,
     CASE WHEN mention.reading_block_id IN $preferred_reading_block_ids
          THEN 0 ELSE 1 END AS preferred_rank
ORDER BY node_id, preferred_rank, mention.evidence_resource_id,
         mention.evidence_start_offset, mention.evidence_id
WITH node_id, collect(mention)[..$limit_per_node] AS mentions
UNWIND mentions AS mention
RETURN mention.mention_id AS mention_id,
       node_id,
       mention.evidence_id AS evidence_id,
       mention.evidence_resource_id AS resource_id,
       mention.source_content_revision AS content_revision,
       mention.reading_block_id AS reading_block_id,
       mention.evidence_quote AS quote,
       mention.evidence_start_offset AS start_offset,
       mention.evidence_end_offset AS end_offset
ORDER BY node_id, mention.evidence_resource_id,
         mention.evidence_start_offset, mention.evidence_id
"""

_PATH_PATTERNS = {
    (
        TraversalDirection.OUT,
        1,
    ): "(seed)-[:RAG_V2_KNOWLEDGE_RELATION*1]->(target)",
    (
        TraversalDirection.OUT,
        2,
    ): "(seed)-[:RAG_V2_KNOWLEDGE_RELATION*1..2]->(target)",
    (
        TraversalDirection.IN,
        1,
    ): "(seed)<-[:RAG_V2_KNOWLEDGE_RELATION*1]-(target)",
    (
        TraversalDirection.IN,
        2,
    ): "(seed)<-[:RAG_V2_KNOWLEDGE_RELATION*1..2]-(target)",
    (
        TraversalDirection.BOTH,
        1,
    ): "(seed)-[:RAG_V2_KNOWLEDGE_RELATION*1]-(target)",
    (
        TraversalDirection.BOTH,
        2,
    ): "(seed)-[:RAG_V2_KNOWLEDGE_RELATION*1..2]-(target)",
}


class Neo4jKnowledgeGraphRepository(KnowledgeGraphRepository):
    """管理 v2 Neo4j 知识图谱的发布生命周期和查询。"""

    def __init__(
        self,
        *,
        driver: AsyncDriver,
        database: str,
        subgraph_cache: GraphQuerySubgraphCache,
    ) -> None:
        if not database.strip():
            raise ValueError("database must not be empty")
        self._driver = driver
        self._database = database
        self._subgraph_cache = subgraph_cache

    async def initialize(self) -> None:
        for query in _SCHEMA_QUERIES:
            await self._driver.execute_query(query, database_=self._database)

    async def begin_build(
        self,
        *,
        resource_id: str,
        content_revision: str,
        document_version: int,
    ) -> None:
        # 构建开始即切换 epoch，避免构建窗口读到上一版候选子图。
        await self._subgraph_cache.bump_epoch()
        result = await self._driver.execute_query(
            _BEGIN_BUILD,
            node_id=resource_node_id(resource_id),
            resource_id=resource_id,
            content_revision=content_revision,
            document_version=document_version,
            database_=self._database,
        )
        if not result.records:
            raise KnowledgeGraphRevisionSupersededError(
                f"content revision {content_revision} was superseded"
            )

    async def publish(
        self,
        *,
        graph: KnowledgeGraph,
        document_version: int,
    ) -> None:
        common = {
            "resource_id": graph.resource_id,
            "content_revision": graph.content_revision,
            "graph_revision": graph.graph_revision,
            "document_version": document_version,
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
                for node in graph.nodes
                if node.kind is KnowledgeNodeKind.ENTITY
                and node.entity_type is not None
            ],
            **common,
        )
        await self._driver.execute_query(
            _UPSERT_EXTERNAL_SOURCES,
            nodes=[
                {"node_id": node.node_id, "label": node.label}
                for node in graph.nodes
                if node.kind is KnowledgeNodeKind.EXTERNAL_SOURCE
            ],
            **common,
        )
        await self._driver.execute_query(
            _UPSERT_RELATIONS,
            relations=[
                {
                    "edge_id": relation.edge_id,
                    "source_node_id": relation.source_node_id,
                    "target_node_id": relation.target_node_id,
                    "relation_type": relation.relation_type.value,
                    "predicate": relation.predicate,
                    # Neo4j relationship properties不能存嵌套对象；并行数组由读取适配器
                    # 校验等长后重建 GraphEvidence，避免 quote 与坐标失去关联。
                    "evidence_ids": [
                        evidence.evidence_id for evidence in relation.evidence
                    ],
                    "evidence_resource_ids": [
                        evidence.resource_id for evidence in relation.evidence
                    ],
                    "evidence_content_revisions": [
                        evidence.content_revision for evidence in relation.evidence
                    ],
                    "evidence_reading_block_ids": [
                        evidence.reading_block_id for evidence in relation.evidence
                    ],
                    "evidence_quotes": [
                        evidence.quote for evidence in relation.evidence
                    ],
                    "evidence_start_offsets": [
                        evidence.source_span.start_offset
                        for evidence in relation.evidence
                    ],
                    "evidence_end_offsets": [
                        evidence.source_span.end_offset
                        for evidence in relation.evidence
                    ],
                }
                for relation in graph.relations
            ],
            **common,
        )
        await self._driver.execute_query(
            _UPSERT_MENTIONS,
            mentions=[
                {
                    "mention_id": mention.mention_id,
                    "node_id": mention.node_id,
                    "reading_block_id": mention.evidence.reading_block_id,
                    "evidence_id": mention.evidence.evidence_id,
                    "evidence_quote": mention.evidence.quote,
                    "evidence_start_offset": (
                        mention.evidence.source_span.start_offset
                    ),
                    "evidence_end_offset": mention.evidence.source_span.end_offset,
                }
                for mention in graph.mentions
            ],
            **common,
        )
        result = await self._driver.execute_query(_PUBLISH, **common)
        if not result.records:
            raise KnowledgeGraphRevisionSupersededError(
                f"content revision {graph.content_revision} was superseded"
            )
        await self._driver.execute_query(_DELETE_OLD_RELATIONS, **common)
        await self._driver.execute_query(_DELETE_OLD_MENTIONS, **common)
        await self._driver.execute_query(
            _DELETE_ORPHAN_NODES,
            database_=self._database,
        )
        await self._subgraph_cache.bump_epoch()

    async def skip(
        self,
        *,
        resource_id: str,
        content_revision: str,
        document_version: int,
    ) -> None:
        common = {
            "resource_id": resource_id,
            "content_revision": content_revision,
            "document_version": document_version,
            "database_": self._database,
        }
        await self.begin_build(
            resource_id=resource_id,
            content_revision=content_revision,
            document_version=document_version,
        )
        await self._driver.execute_query(
            _DELETE_OLD_RELATIONS,
            graph_revision="",
            **common,
        )
        await self._driver.execute_query(
            _DELETE_OLD_MENTIONS,
            graph_revision="",
            **common,
        )
        result = await self._driver.execute_query(_SKIP, **common)
        if not result.records:
            raise KnowledgeGraphRevisionSupersededError(
                f"content revision {content_revision} was superseded"
            )
        await self._driver.execute_query(
            _DELETE_ORPHAN_NODES,
            database_=self._database,
        )
        await self._subgraph_cache.bump_epoch()

    async def delete_resources(self, resource_ids: Sequence[str]) -> None:
        ids = list(dict.fromkeys(resource_ids))
        if not ids:
            return
        await self._driver.execute_query(
            _DELETE_RESOURCES,
            resource_ids=ids,
            database_=self._database,
        )
        await self._driver.execute_query(
            _DELETE_ORPHAN_NODES,
            database_=self._database,
        )
        await self._driver.execute_query(
            _DELETE_ORPHAN_GROUP_ACLS,
            database_=self._database,
        )
        await self._subgraph_cache.bump_epoch()

    async def find_nodes(
        self,
        *,
        reading_blocks: Sequence[GraphSeedBlock],
        permission_scope: PermissionScope,
        limit: int,
    ) -> list[KnowledgeNode]:
        if not reading_blocks or limit <= 0:
            return []

        blocks_by_key: dict[tuple[str, str, str], GraphSeedBlock] = {}
        for block in reading_blocks:
            key = (
                block.resource_id,
                block.content_revision,
                block.reading_block_id,
            )
            item = blocks_by_key.get(key)
            if item is None:
                item = GraphSeedBlock(
                    resource_id=block.resource_id,
                    content_revision=block.content_revision,
                    reading_block_id=block.reading_block_id,
                    rank=block.rank,
                )
                blocks_by_key[key] = item
            else:
                item.rank = min(item.rank, block.rank)
            for span in block.matched_source_spans:
                if span not in item.matched_source_spans:
                    item.matched_source_spans.append(span)

        query_blocks = [
            {
                "resource_id": block.resource_id,
                "content_revision": block.content_revision,
                "reading_block_id": block.reading_block_id,
                "rank": block.rank,
                "matched_source_spans": [
                    {
                        "start_offset": span.start_offset,
                        "end_offset": span.end_offset,
                    }
                    for span in block.matched_source_spans
                ],
            }
            for block in blocks_by_key.values()
        ]

        acl_filter, acl_parameters = _acl_predicate(
            permission_scope,
            resource_alias="resource",
        )
        result = await self._driver.execute_query(
            _FIND_NODES.format(acl_filter=acl_filter),
            reading_blocks=query_blocks,
            limit=limit,
            published_status=GraphStatus.PUBLISHED.value,
            **acl_parameters,
            database_=self._database,
            routing_=RoutingControl.READ,
        )
        return [_to_knowledge_node(record) for record in result.records]

    async def _find_mentions_uncached(
        self,
        *,
        node_ids: Sequence[str],
        resource_ids: Sequence[str],
        preferred_reading_block_ids: Sequence[str],
        permission_scope: PermissionScope,
        limit_per_node: int,
    ) -> list[KnowledgeMention]:
        if not node_ids or not resource_ids or limit_per_node <= 0:
            return []

        acl_filter, acl_parameters = _acl_predicate(
            permission_scope,
            resource_alias="resource",
        )
        result = await self._driver.execute_query(
            _FIND_MENTIONS.format(acl_filter=acl_filter),
            node_ids=list(dict.fromkeys(node_ids)),
            resource_ids=list(dict.fromkeys(resource_ids)),
            preferred_reading_block_ids=list(
                dict.fromkeys(preferred_reading_block_ids)
            ),
            limit_per_node=limit_per_node,
            published_status=GraphStatus.PUBLISHED.value,
            **acl_parameters,
            database_=self._database,
            routing_=RoutingControl.READ,
        )
        return [_to_mention(record) for record in result.records]

    async def _find_paths_uncached(
        self,
        *,
        seed_node_ids: Sequence[str],
        permission_scope: PermissionScope,
        relation_types: Sequence[KnowledgeRelationType] = (),
        direction: TraversalDirection = TraversalDirection.BOTH,
        max_depth: int = 1,
        limit: int = 40,
    ) -> list[TraversedPath]:
        if not seed_node_ids or limit <= 0:
            return []
        pattern = _PATH_PATTERNS.get((direction, max_depth))
        if pattern is None:
            raise ValueError("graph traversal depth must be 1 or 2")

        evidence_acl, acl_parameters = _acl_predicate(
            permission_scope,
            resource_alias="evidence",
        )
        path_node_acl, _ = _acl_predicate(
            permission_scope,
            resource_alias="path_node",
        )
        result = await self._driver.execute_query(
            f"""
            MATCH (seed:RagV2Node)
            WHERE seed.node_id IN $seed_node_ids
            MATCH path={pattern}
            WHERE target <> seed
              AND all(path_node IN nodes(path)
                WHERE single(other IN nodes(path) WHERE other = path_node))
              AND all(path_node IN nodes(path)
                WHERE NOT path_node:RagV2ResourceNode OR {path_node_acl})
              AND all(relation IN relationships(path)
                WHERE (size($relation_types) = 0
                       OR relation.relation_type IN $relation_types)
                  AND EXISTS {{
                    MATCH (evidence:RagV2ResourceNode {{
                      resource_id: relation.evidence_resource_id
                    }})
                    WHERE evidence.graph_status = '{GraphStatus.PUBLISHED.value}'
                      AND evidence.content_revision = relation.source_content_revision
                      AND evidence.graph_revision = relation.graph_revision
                      AND {evidence_acl}
                  }})
            RETURN [path_node IN nodes(path) | {{
                     node_id: path_node.node_id,
                     kind: CASE
                       WHEN path_node:RagV2EntityNode THEN 'Entity'
                       WHEN path_node:RagV2ExternalSourceNode THEN 'ExternalSource'
                       ELSE 'Resource'
                     END,
                     label: coalesce(path_node.label, path_node.resource_id),
                     entity_type: path_node.entity_type,
                     resource_id: path_node.resource_id
                   }}] AS nodes,
                   [relation IN relationships(path) | {{
                     edge_id: relation.edge_id,
                     source_node_id: startNode(relation).node_id,
                     target_node_id: endNode(relation).node_id,
                     relation_type: relation.relation_type,
                     predicate: relation.predicate,
                     evidence_ids: relation.evidence_ids,
                     evidence_resource_ids: relation.evidence_resource_ids,
                     evidence_content_revisions:
                       relation.evidence_content_revisions,
                     evidence_reading_block_ids:
                       relation.evidence_reading_block_ids,
                     evidence_quotes: relation.evidence_quotes,
                     evidence_start_offsets: relation.evidence_start_offsets,
                     evidence_end_offsets: relation.evidence_end_offsets
                   }}] AS edges
            ORDER BY size(edges), nodes[-1].node_id
            LIMIT $limit
            """,
            seed_node_ids=list(dict.fromkeys(seed_node_ids)),
            relation_types=[item.value for item in relation_types],
            limit=limit,
            **acl_parameters,
            database_=self._database,
            routing_=RoutingControl.READ,
        )
        return [_to_path(record) for record in result.records]

    async def find_subgraph(
        self,
        *,
        seed_node_ids: Sequence[str],
        permission_scope: PermissionScope,
        relation_types: Sequence[KnowledgeRelationType] = (),
        direction: TraversalDirection = TraversalDirection.BOTH,
        max_depth: int = 1,
        path_limit: int = 40,
        mention_limit_per_node: int = 3,
    ) -> GraphQuerySubgraph:
        """一次查询路径并批量补齐所有路径所需的 mention。"""
        canonical_limit = self._subgraph_cache.canonical_path_limit
        query_limit = min(max(path_limit, canonical_limit), canonical_limit)

        async def load() -> GraphQuerySubgraph:
            started = perf_counter()
            paths = await self._find_paths_uncached(
                seed_node_ids=seed_node_ids,
                permission_scope=permission_scope,
                relation_types=relation_types,
                direction=direction,
                max_depth=max_depth,
                limit=query_limit,
            )
            debug(
                "neo4j_find_subgraph_latency",
                duration_seconds=perf_counter() - started,
            )
            node_ids = list(
                dict.fromkeys(
                    node.node_id
                    for path in paths
                    for node in path.nodes
                    if node.kind is not KnowledgeNodeKind.RESOURCE
                )
            )
            resource_ids = sorted(
                {
                    resource_id
                    for path in paths
                    for resource_id in _path_resource_ids(path)
                }
            )
            preferred_blocks = list(
                dict.fromkeys(
                    evidence.reading_block_id
                    for path in paths
                    for edge in path.edges
                    for evidence in edge.evidence
                )
            )
            mention_started = perf_counter()
            mentions = await self._find_mentions_uncached(
                node_ids=node_ids,
                resource_ids=resource_ids,
                preferred_reading_block_ids=preferred_blocks,
                permission_scope=permission_scope,
                limit_per_node=mention_limit_per_node,
            )
            debug(
                "neo4j_find_mentions_latency",
                duration_seconds=perf_counter() - mention_started,
            )
            return GraphQuerySubgraph(
                paths=paths,
                mentions=mentions,
                seed_node_ids=list(dict.fromkeys(seed_node_ids)),
                relation_types=list(dict.fromkeys(relation_types)),
                direction=direction,
                max_depth=max_depth,
                path_limit=query_limit,
                mention_limit_per_node=mention_limit_per_node,
            )

        subgraph = await self._subgraph_cache.get_or_load(
            seed_node_ids=seed_node_ids,
            permission_scope=permission_scope,
            relation_types=relation_types,
            direction=direction,
            max_depth=max_depth,
            path_limit=path_limit,
            mention_limit_per_node=mention_limit_per_node,
            loader=load,
        )
        return replace(
            subgraph,
            paths=subgraph.paths[:path_limit],
            path_limit=min(path_limit, len(subgraph.paths)),
        )


def _to_path(record) -> TraversedPath:
    return TraversedPath(
        nodes=[_to_knowledge_node(item) for item in record["nodes"]],
        edges=[_to_edge(item) for item in record["edges"]],
    )


def _path_resource_ids(path: TraversedPath) -> set[str]:
    return {
        *(
            evidence.resource_id
            for edge in path.edges
            for evidence in edge.evidence
        ),
        *(node.resource_id for node in path.nodes if node.resource_id is not None),
    }


def _to_knowledge_node(item) -> KnowledgeNode:
    kind = KnowledgeNodeKind(item["kind"])
    return KnowledgeNode(
        node_id=item["node_id"],
        kind=kind,
        label=item["label"],
        entity_type=(
            KnowledgeEntityType(item["entity_type"])
            if kind is KnowledgeNodeKind.ENTITY
            else None
        ),
        resource_id=(
            item["resource_id"] if kind is KnowledgeNodeKind.RESOURCE else None
        ),
    )


def _to_edge(item) -> TraversedEdge:
    return TraversedEdge(
        edge_id=item["edge_id"],
        source_node_id=item["source_node_id"],
        target_node_id=item["target_node_id"],
        relation_type=KnowledgeRelationType(item["relation_type"]),
        predicate=item.get("predicate"),
        evidence=_to_evidence(item),
    )


def _to_mention(item) -> KnowledgeMention:
    return KnowledgeMention(
        mention_id=item["mention_id"],
        node_id=item["node_id"],
        evidence=GraphEvidence(
            evidence_id=item["evidence_id"],
            resource_id=item["resource_id"],
            content_revision=item["content_revision"],
            reading_block_id=item["reading_block_id"],
            source_span=SourceSpan(item["start_offset"], item["end_offset"]),
            quote=item["quote"],
        ),
    )


def _to_evidence(item) -> list[GraphEvidence]:
    """校验 Neo4j 并行数组并重建不可拆分的图谱证据对象。"""
    fields = [
        list(item["evidence_ids"]),
        list(item["evidence_resource_ids"]),
        list(item["evidence_content_revisions"]),
        list(item["evidence_reading_block_ids"]),
        list(item["evidence_quotes"]),
        list(item["evidence_start_offsets"]),
        list(item["evidence_end_offsets"]),
    ]
    evidence_count = len(fields[0])
    if not evidence_count or any(
        len(values) != evidence_count for values in fields[1:]
    ):
        raise ValueError("graph relation evidence arrays are empty or misaligned")

    return [
        GraphEvidence(
            evidence_id=evidence_id,
            resource_id=resource_id,
            content_revision=content_revision,
            reading_block_id=reading_block_id,
            source_span=SourceSpan(start_offset, end_offset),
            quote=quote,
        )
        for (
            evidence_id,
            resource_id,
            content_revision,
            reading_block_id,
            quote,
            start_offset,
            end_offset,
        ) in zip(*fields, strict=True)
    ]


def _acl_predicate(
    scope: PermissionScope,
    *,
    resource_alias: str,
) -> tuple[str, dict[str, object]]:
    """生成与 ResourceAcl.can_read 同语义的 Neo4j 查询条件。"""
    return (
        f"""(
          {resource_alias}.owner_id = $acl_user_id
          OR $acl_user_id IN coalesce({resource_alias}.readable_users, [])
          OR (
            NOT $acl_user_id IN coalesce({resource_alias}.excluded_read_users, [])
            AND (
              EXISTS {{
                MATCH ({resource_alias})-[:RAG_V2_HAS_GROUP_ACL]->(managed:RagV2ResourceGroupAcl)
                WHERE managed.group_id IN $acl_managed_group_ids
              }}
              OR EXISTS {{
                MATCH ({resource_alias})-[:RAG_V2_HAS_GROUP_ACL]->(joined:RagV2ResourceGroupAcl)
                WHERE joined.group_id IN $acl_joined_group_ids
                  AND (
                    (joined.is_readable = true
                     AND NOT $acl_user_id IN coalesce(joined.excluded_read_users, []))
                    OR (joined.is_readable = false
                        AND $acl_user_id IN coalesce(joined.readable_users, []))
                  )
              }}
            )
          )
        )""",
        {
            "acl_user_id": scope.user_id,
            "acl_managed_group_ids": list(scope.managed_group_ids),
            "acl_joined_group_ids": list(scope.joined_group_ids),
        },
    )

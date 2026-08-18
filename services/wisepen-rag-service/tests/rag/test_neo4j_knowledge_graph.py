from dataclasses import dataclass

import pytest

from rag.core.persistence.neo4j import Neo4jKnowledgeGraphRepository
from rag.domain.models.acl import PermissionScope
from rag.domain.models.graph import (
    GraphEvidence,
    KnowledgeEntityType,
    KnowledgeGraph,
    KnowledgeMention,
    KnowledgeNode,
    KnowledgeNodeKind,
    KnowledgeRelation,
    KnowledgeRelationType,
    TraversalDirection,
)
from rag.domain.repositories import KnowledgeGraphRevisionSupersededError
from rag.domain.repositories.neo4j import GraphQuerySubgraph, GraphSeedBlock
from common.utils.document import SourceSpan


@dataclass
class _Result:
    records: list[dict]


class _Driver:
    def __init__(
        self,
        records: list[dict] | None = None,
        *,
        cas_succeeds: bool = True,
        mention_records: list[dict] | None = None,
    ) -> None:
        self.records = records or []
        self.cas_succeeds = cas_succeeds
        self.mention_records = mention_records
        self.calls: list[tuple[str, dict]] = []

    async def execute_query(self, query: str, **parameters):
        self.calls.append((query, parameters))
        if "RAG_V2_MENTION" in query and self.mention_records is not None:
            return _Result(self.mention_records)
        if "graph_status = 'published'" in query and not self.cas_succeeds:
            return _Result(records=[])
        if "RETURN resource.resource_id AS resource_id" in query:
            return _Result(records=[{"resource_id": "resource-1"}])
        return _Result(self.records)


class _Cache:
    canonical_path_limit = 80

    async def get_or_load(self, **kwargs):
        return await kwargs["loader"]()

    async def bump_epoch(self):
        return "1"


def _repository(driver):
    return Neo4jKnowledgeGraphRepository(
        driver=driver,
        database="rag-v2",
        subgraph_cache=_Cache(),
    )


@pytest.mark.asyncio
async def test_repository_initializes_v2_schema() -> None:
    driver = _Driver()
    repository = _repository(driver)

    await repository.initialize()

    queries = [query for query, _ in driver.calls]
    assert len(queries) == 3
    assert "CREATE CONSTRAINT rag_v2_node_id" in queries[0]
    assert "CREATE INDEX rag_v2_relation_resource" in queries[1]
    assert "CREATE INDEX rag_v2_mention_resource" in queries[2]
    assert all(parameters["database_"] == "rag-v2" for _, parameters in driver.calls)


@pytest.mark.asyncio
async def test_repository_uses_v2_namespace_and_publishes_last() -> None:
    driver = _Driver()
    repository = _repository(driver)

    await repository.begin_build(
        resource_id="resource-1",
        content_revision="revision-1",
        document_version=3,
    )
    await repository.publish(graph=_graph(), document_version=3)

    queries = [query for query, _ in driver.calls]
    assert "RagV2ResourceNode" in queries[0]
    assert any("RAG_V2_KNOWLEDGE_RELATION" in query for query in queries)
    relation_call = next(
        parameters for query, parameters in driver.calls if "UNWIND $relations" in query
    )
    assert all("relation_id" not in item for item in relation_call["relations"])
    assert relation_call["relations"][0]["evidence_ids"] == ["evidence-1"]
    assert relation_call["relations"][0]["evidence_reading_block_ids"] == [
        "block-1"
    ]
    assert any("RAG_V2_MENTION" in query for query in queries)
    publish_index = next(
        index
        for index, query in enumerate(queries)
        if "graph_status = 'published'" in query
    )
    assert publish_index == max(
        index
        for index, query in enumerate(queries)
        if "RETURN resource.resource_id" in query
    )
    assert all(parameters["database_"] == "rag-v2" for _, parameters in driver.calls)


@pytest.mark.asyncio
async def test_repository_rejects_superseded_publish() -> None:
    driver = _Driver(cas_succeeds=False)
    repository = _repository(driver)

    with pytest.raises(KnowledgeGraphRevisionSupersededError):
        await repository.publish(graph=_graph(), document_version=3)


@pytest.mark.asyncio
async def test_repository_skips_revision_after_clearing_old_graph() -> None:
    driver = _Driver()
    repository = _repository(driver)

    await repository.skip(
        resource_id="resource-1",
        content_revision="revision-flat",
        document_version=4,
    )

    queries = [query for query, _ in driver.calls]
    skip_index = next(
        index
        for index, query in enumerate(queries)
        if "graph_status = 'skipped'" in query
    )
    assert skip_index > 0
    assert any("DELETE relation" in query for query in queries)
    assert any("DELETE mention" in query for query in queries)
    assert "graph_status = 'skipped'" in queries[skip_index]


@pytest.mark.asyncio
async def test_repository_deletes_resource_group_acl_orphans() -> None:
    driver = _Driver()
    repository = _repository(driver)

    await repository.delete_resources(["resource-1"])

    queries = [query for query, _ in driver.calls]
    assert "DETACH DELETE resource" in queries[0]
    assert "RagV2ResourceGroupAcl" in queries[-1]


@pytest.mark.asyncio
async def test_find_nodes_filters_published_revision_and_deduplicates() -> None:
    driver = _Driver(
        records=[
            {
                "node_id": "node-1",
                "kind": "Entity",
                "label": "Alpha",
                "entity_type": "concept",
                "resource_id": None,
            }
        ]
    )
    repository = _repository(driver)
    block = _seed_block()

    nodes = await repository.find_nodes(
        reading_blocks=[block, block],
        permission_scope=PermissionScope(user_id="user-1"),
        limit=3,
    )

    query, parameters = driver.calls[0]
    assert "resource.graph_status = $published_status" in query
    assert "resource.owner_id = $acl_user_id" in query
    assert "resource.content_revision = item.content_revision" in query
    assert "mention.graph_revision = resource.graph_revision" in query
    assert parameters["reading_blocks"] == [
        {
            "resource_id": "resource-1",
            "content_revision": "revision-1",
            "reading_block_id": "block-1",
            "rank": 0,
            "matched_source_spans": [
                {"start_offset": 0, "end_offset": 5}
            ],
        }
    ]
    assert parameters["limit"] == 3
    assert parameters["published_status"] == "published"
    assert nodes[0].kind is KnowledgeNodeKind.ENTITY
    assert nodes[0].entity_type is KnowledgeEntityType.CONCEPT


@pytest.mark.asyncio
async def test_find_nodes_does_not_query_when_limit_is_zero() -> None:
    driver = _Driver()
    repository = _repository(driver)

    nodes = await repository.find_nodes(
        reading_blocks=[_seed_block()],
        permission_scope=PermissionScope(user_id="user-1"),
        limit=0,
    )

    assert nodes == []
    assert driver.calls == []


@pytest.mark.asyncio
async def test_find_subgraph_batches_mentions_after_path_query() -> None:
    driver = _Driver(
        records=[
            {
                "mention_id": "mention-1",
                "node_id": "node-1",
                "evidence_id": "evidence-1",
                "resource_id": "resource-1",
                "content_revision": "revision-1",
                "reading_block_id": "block-1",
                "quote": "Alpha",
                "start_offset": 0,
                "end_offset": 5,
            }
        ]
    )
    driver.records = [_path_record()]
    driver.mention_records = []
    repository = _repository(driver)

    subgraph = await repository.find_subgraph(
        seed_node_ids=["node-a"],
        permission_scope=PermissionScope(user_id="user-1"),
        path_limit=3,
        mention_limit_per_node=3,
    )

    assert isinstance(subgraph, GraphQuerySubgraph)
    assert len(driver.calls) == 2
    assert "RAG_V2_MENTION" in driver.calls[1][0]
    assert driver.calls[1][1]["limit_per_node"] == 3


@pytest.mark.asyncio
async def test_find_subgraph_uses_bounded_direction_revision_and_cycle_filter() -> None:
    driver = _Driver(records=[_path_record()])
    driver.mention_records = []
    repository = _repository(driver)

    subgraph = await repository.find_subgraph(
        seed_node_ids=["node-a"],
        permission_scope=PermissionScope(user_id="user-1"),
        relation_types=[KnowledgeRelationType.DEPENDS_ON],
        direction=TraversalDirection.OUT,
        max_depth=2,
        path_limit=5,
    )

    query, parameters = driver.calls[0]
    assert "*1..2]->(target)" in query
    assert "RAG_V2_MENTION" not in query
    assert "single(other IN nodes(path)" in query
    assert "evidence.graph_status = 'published'" in query
    assert "evidence.graph_revision = relation.graph_revision" in query
    assert parameters["relation_types"] == ["DEPENDS_ON"]
    assert subgraph.paths[0].edges[0].edge_id == "edge-1"


@pytest.mark.asyncio
async def test_find_subgraph_does_not_query_without_seed_nodes() -> None:
    driver = _Driver(records=[_path_record()])
    repository = _repository(driver)

    subgraph = await repository.find_subgraph(
        seed_node_ids=[],
        permission_scope=PermissionScope(user_id="user-1"),
    )

    assert subgraph.paths == []
    assert driver.calls == []


@pytest.mark.asyncio
async def test_find_subgraph_rejects_misaligned_persisted_evidence_arrays() -> None:
    record = _path_record()
    record["edges"][0]["evidence_end_offsets"] = []
    repository = Neo4jKnowledgeGraphRepository(
        driver=_Driver(records=[record]),
        database="rag-v2",
        subgraph_cache=_Cache(),
    )

    with pytest.raises(ValueError, match="misaligned"):
        await repository.find_subgraph(
            seed_node_ids=["node-a"],
            permission_scope=PermissionScope(user_id="user-1"),
        )


def _graph() -> KnowledgeGraph:
    evidence = _graph_evidence()
    return KnowledgeGraph(
        resource_id="resource-1",
        content_revision="revision-1",
        graph_revision="graph-1",
        nodes=[
            KnowledgeNode(
                node_id="entity-1",
                kind=KnowledgeNodeKind.ENTITY,
                label="Alpha",
                entity_type=KnowledgeEntityType.CONCEPT,
            ),
            KnowledgeNode(
                node_id="entity-2",
                kind=KnowledgeNodeKind.ENTITY,
                label="Beta",
                entity_type=KnowledgeEntityType.CONCEPT,
            )
        ],
        mentions=[
            KnowledgeMention(
                mention_id="mention-1",
                node_id="entity-1",
                evidence=evidence,
            )
        ],
        relations=[
            KnowledgeRelation(
                edge_id="edge-1",
                source_node_id="entity-1",
                target_node_id="entity-2",
                relation_type=KnowledgeRelationType.DEPENDS_ON,
                evidence=[evidence],
            )
        ],
    )


def _seed_block() -> GraphSeedBlock:
    return GraphSeedBlock(
        resource_id="resource-1",
        content_revision="revision-1",
        reading_block_id="block-1",
        rank=0,
        matched_source_spans=[SourceSpan(0, 5)],
    )


def _graph_evidence() -> GraphEvidence:
    return GraphEvidence(
        evidence_id="evidence-1",
        resource_id="resource-1",
        content_revision="revision-1",
        reading_block_id="block-1",
        source_span=SourceSpan(0, 5),
        quote="Alpha",
    )


def _path_record() -> dict:
    return {
        "nodes": [
            {
                "node_id": "node-a",
                "kind": "Entity",
                "label": "Alpha",
                "entity_type": "concept",
                "resource_id": None,
            },
            {
                "node_id": "node-b",
                "kind": "Entity",
                "label": "Beta",
                "entity_type": "concept",
                "resource_id": None,
            },
        ],
        "edges": [
            {
                "edge_id": "edge-1",
                "source_node_id": "node-a",
                "target_node_id": "node-b",
                "relation_type": "DEPENDS_ON",
                "predicate": None,
                "evidence_ids": ["evidence-1"],
                "evidence_resource_ids": ["resource-1"],
                "evidence_content_revisions": ["revision-1"],
                "evidence_reading_block_ids": ["block-1"],
                "evidence_quotes": ["Alpha depends on Beta."],
                "evidence_start_offsets": [0],
                "evidence_end_offsets": [22],
            }
        ],
    }

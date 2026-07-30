from __future__ import annotations

from dataclasses import dataclass

import pytest

from rag.application.rag.acl import (
    RagComputedGroupAclProjection,
    RagResourceAclProjection,
)
from rag.application.rag.graph_extraction import (
    KnowledgeEntityType,
    KnowledgeNodeKind,
    KnowledgeRelationType,
)
from rag.application.rag.graph_projection import (
    KnowledgeEdge,
    KnowledgeGraphProjection,
    KnowledgeMention,
    KnowledgeNode,
    resource_node_id,
)
from rag.application.rag.knowledge_navigation import (
    KnowledgeGraphExpandRequest,
    KnowledgeMentionSource,
    KnowledgeNavigationDirection,
)
from rag.application.rag.retrieval import (
    RagPermissionFilterBuilder,
    RagPermissionScope,
)
from rag.application.rag.repositories import KnowledgeGraphProjectionSupersededError
from common.core.domain import GroupRoleType
from rag.core.persistence.neo4j import Neo4jKnowledgeGraphRepository


@dataclass
class _Result:
    records: list[dict]


class _Driver:
    def __init__(
        self,
        *,
        applied: bool = False,
        records: list[dict] | None = None,
        revision_current: bool = True,
    ) -> None:
        self.applied = applied
        self.records = records or []
        self.revision_current = revision_current
        self.calls: list[tuple[str, dict]] = []

    async def execute_query(self, query: str, **kwargs):
        self.calls.append((query, kwargs))
        if " AS applied" in query:
            return _Result(records=[{"applied": self.applied}])
        if "AS revision_applied" in query:
            return _Result(
                records=[{"revision_applied": True}] if self.revision_current else []
            )
        if "MATCH path=" in query or "RETURN DISTINCT node.node_id" in query:
            return _Result(records=self.records)
        return _Result(records=[])


class _PermissionAuthorizer:
    def __init__(self, denied_resource_ids: tuple[str, ...] = ()) -> None:
        self.denied_resource_ids = frozenset(denied_resource_ids)

    async def accessible_resource_ids(self, resource_ids, scope):
        return frozenset(resource_ids) - self.denied_resource_ids


@pytest.mark.asyncio
async def test_repository_writes_acl_evidence_and_switches_revision_last() -> None:
    driver = _Driver()
    repository = Neo4jKnowledgeGraphRepository(
        driver=driver,
        database="neo4j",
        permission_authorizer=_PermissionAuthorizer(),
        permission_filter_builder=RagPermissionFilterBuilder(),
    )
    projection = _projection()
    acl = _acl()

    await repository.update_acl_projection(acl)
    await repository.apply_projection(projection=projection)

    assert len(driver.calls) == 8
    acl_query, acl_params = driver.calls[0]
    assert "HAS_GROUP_ACL" in acl_query
    assert acl_params["node_id"] == resource_node_id("resource-1")
    assert acl_params["acl_revision"] == 42
    assert acl_params["owner_id"] == "owner-1"
    assert "resource.acl_revision <= $acl_revision" in acl_query
    assert acl_params["group_acls"] == [
        {
            "acl_id": "resource-1:group-1",
            "group_id": "group-1",
            "is_readable": True,
            "readable_users": ["group-user"],
            "excluded_read_users": ["excluded-user"],
        }
    ]

    relation_call = next(
        params
        for query, params in driver.calls
        if "KNOWLEDGE_RELATION" in query and "UNWIND $edges" in query
    )
    assert relation_call["edges"][0]["relation_type"] == "DEPENDS_ON"
    assert "evidence_ref_ids" not in relation_call["edges"][0]
    assert relation_call["edges"][0]["evidence_quotes"] == ["evidence"]
    assert relation_call["edges"][0]["evidence_source_ref_ids"] == ["source-1"]
    mention_call = next(
        params for query, params in driver.calls if "UNWIND $mentions" in query
    )
    assert mention_call["mentions"][0]["chunk_id"] == "chunk-1"
    assert mention_call["mentions"][0]["evidence_quote"] == "evidence"
    assert "applied_relation_revision" in driver.calls[5][0]
    assert "DELETE relation" in driver.calls[6][0]
    assert "DELETE mention" in driver.calls[7][0]


@pytest.mark.asyncio
async def test_repository_rejects_superseded_content_revision() -> None:
    repository = _repository(_Driver(revision_current=False))

    with pytest.raises(KnowledgeGraphProjectionSupersededError):
        await repository.apply_projection(projection=_projection())


@pytest.mark.asyncio
async def test_delete_resources_removes_evidence_before_resource_nodes() -> None:
    driver = _Driver()
    repository = _repository(driver)

    await repository.delete_resources(("resource-1", "resource-2"))

    assert len(driver.calls) == 4
    assert "KNOWLEDGE_RELATION" in driver.calls[0][0]
    assert "DETACH DELETE resource" in driver.calls[1][0]
    assert "ResourceGroupAcl" in driver.calls[2][0]
    assert "NOT (node)--()" in driver.calls[3][0]
    assert driver.calls[0][1]["resource_ids"] == [
        "resource-1",
        "resource-2",
    ]


@pytest.mark.asyncio
async def test_repository_initializes_schema_and_checks_applied_revision() -> None:
    driver = _Driver(applied=True)
    repository = Neo4jKnowledgeGraphRepository(
        driver=driver,
        database="knowledge",
        permission_authorizer=_PermissionAuthorizer(),
        permission_filter_builder=RagPermissionFilterBuilder(),
    )

    await repository.initialize()
    applied = await repository.is_projection_applied(
        resource_id="resource-1",
        content_revision="revision-1",
    )

    assert applied is True
    assert len(driver.calls) == 5
    assert all(params["database_"] == "knowledge" for _, params in driver.calls)


@pytest.mark.asyncio
async def test_resolve_mentions_applies_acl_and_current_revision() -> None:
    driver = _Driver(
        records=[
            {
                "node_id": "kn_alpha",
                "kind": "Entity",
                "label": "Alpha",
                "entity_type": "concept",
            }
        ]
    )
    repository = _repository(driver)

    nodes = await repository.resolve_mentions(
        sources=(KnowledgeMentionSource("resource-1", "chunk-1"),),
        permission_scope=_permission_scope(),
    )

    query, params = driver.calls[0]
    assert "resource.applied_relation_revision = mention.relation_revision" in query
    assert "HAS_GROUP_ACL" in query
    assert "checkPermission" not in query
    assert params["rag_acl_user_id"] == "user-1"
    assert nodes[0].node_id == "kn_alpha"


@pytest.mark.asyncio
async def test_expand_uses_bounded_pattern_and_acl_for_endpoints_and_evidence() -> None:
    driver = _Driver(
        records=[
            {
                "nodes": [
                    {
                        "node_id": "kn_alpha",
                        "kind": "Entity",
                        "label": "Alpha",
                        "entity_type": "concept",
                    },
                    {
                        "node_id": "kn_beta",
                        "kind": "Entity",
                        "label": "Beta",
                        "entity_type": "concept",
                    },
                ],
                "edges": [
                    {
                        "edge_id": "kne_1",
                        "source_node_id": "kn_alpha",
                        "target_node_id": "kn_beta",
                        "relation_type": "DEPENDS_ON",
                        "predicate": None,
                        "evidence_resource_id": "resource-1",
                        "evidence_quotes": ["Alpha depends on Beta."],
                        "evidence_source_ref_ids": ["source-1"],
                    }
                ],
            }
        ]
    )
    repository = _repository(driver)

    result = await repository.expand(
        KnowledgeGraphExpandRequest(
            seed_node_ids=("kn_alpha",),
            permission_scope=_permission_scope(),
            known_node_ids=("kn_seen",),
            relation_types=(KnowledgeRelationType.DEPENDS_ON,),
            direction=KnowledgeNavigationDirection.OUT,
            max_depth=2,
            limit=5,
        )
    )

    query, params = driver.calls[0]
    assert "(seed)-[:KNOWLEDGE_RELATION|MENTIONS*1..2]->(target)" in query
    assert query.count("HAS_GROUP_ACL") == 2
    assert "checkPermission" not in query
    assert params["relation_types"] == ["DEPENDS_ON"]
    assert params["known_node_ids"] == ["kn_seen"]
    assert result[0].edges[0].evidence_source_ref_ids == ("source-1",)
    assert result[0].edges[0].evidence_quotes == ("Alpha depends on Beta.",)


@pytest.mark.asyncio
async def test_expand_drops_path_rejected_by_local_acl_gate() -> None:
    driver = _Driver(records=[_path_record()])
    repository = Neo4jKnowledgeGraphRepository(
        driver=driver,
        database="neo4j",
        permission_authorizer=_PermissionAuthorizer(("resource-1",)),
        permission_filter_builder=RagPermissionFilterBuilder(),
    )

    result = await repository.expand(
        KnowledgeGraphExpandRequest(
            seed_node_ids=("kn_alpha",),
            permission_scope=_permission_scope(),
        )
    )

    assert result == ()


def _repository(driver: _Driver) -> Neo4jKnowledgeGraphRepository:
    return Neo4jKnowledgeGraphRepository(
        driver=driver,
        database="neo4j",
        permission_authorizer=_PermissionAuthorizer(),
        permission_filter_builder=RagPermissionFilterBuilder(),
    )


def _permission_scope() -> RagPermissionScope:
    return RagPermissionScope(
        user_id="user-1",
        group_role_map={
            "managed-group": GroupRoleType.ADMIN,
            "joined-group": GroupRoleType.MEMBER,
        },
    )


def _acl() -> RagResourceAclProjection:
    return RagResourceAclProjection(
        resource_id="resource-1",
        acl_revision=42,
        owner_id="owner-1",
        readable_users=("user-1",),
        excluded_read_users=("blocked-user",),
        computed_group_acls=(
            RagComputedGroupAclProjection(
                group_id="group-1",
                is_readable=True,
                readable_users=("group-user",),
                excluded_read_users=("excluded-user",),
            ),
        ),
    )


def _projection() -> KnowledgeGraphProjection:
    resource_id = resource_node_id("resource-1")
    entity_id = "kn_entity"
    return KnowledgeGraphProjection(
        resource_id="resource-1",
        content_revision="revision-1",
        relation_revision="relation-1",
        nodes=(
            KnowledgeNode(
                node_id=resource_id,
                kind=KnowledgeNodeKind.RESOURCE,
                label="resource-1",
                resource_id="resource-1",
            ),
            KnowledgeNode(
                node_id=entity_id,
                kind=KnowledgeNodeKind.ENTITY,
                label="Beta",
                entity_type=KnowledgeEntityType.TECHNOLOGY,
            ),
        ),
        mentions=(
            KnowledgeMention(
                mention_id="mention-1",
                node_id=entity_id,
                chunk_id="chunk-1",
                source_ref_id="source-1",
                evidence_quote="evidence",
            ),
        ),
        edges=(
            KnowledgeEdge(
                edge_id="edge-1",
                source_node_id=resource_id,
                target_node_id=entity_id,
                relation_type=KnowledgeRelationType.DEPENDS_ON,
                predicate=None,
                evidence_quotes=("evidence",),
                evidence_source_ref_ids=("source-1",),
            ),
        ),
    )


def _path_record() -> dict:
    return {
        "nodes": [
            {
                "node_id": "kn_alpha",
                "kind": "Entity",
                "label": "Alpha",
                "entity_type": "concept",
            },
            {
                "node_id": "kn_beta",
                "kind": "Entity",
                "label": "Beta",
                "entity_type": "concept",
            },
        ],
        "edges": [
            {
                "edge_id": "kne_1",
                "source_node_id": "kn_alpha",
                "target_node_id": "kn_beta",
                "relation_type": "DEPENDS_ON",
                "predicate": None,
                "evidence_resource_id": "resource-1",
                "evidence_quotes": ["Alpha depends on Beta."],
                "evidence_source_ref_ids": ["source-1"],
            }
        ],
    }

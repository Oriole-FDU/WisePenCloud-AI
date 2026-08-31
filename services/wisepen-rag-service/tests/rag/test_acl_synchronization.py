from dataclasses import dataclass

import pytest

from rag.application.rag.acl import ResourceAclRefresher
from rag.core.persistence.neo4j import Neo4jGraphAclWriter
from rag.core.persistence.neo4j.knowledge_graph_repository import _acl_predicate
from rag.core.persistence.qdrant import QdrantRetrievalAclWriter
from rag.domain.models.acl import GroupResourceAcl, PermissionScope, ResourceAcl


def _acl() -> ResourceAcl:
    return ResourceAcl(
        resource_id="resource-1",
        acl_revision=7,
        owner_id="owner-1",
        readable_users=["user-1"],
        excluded_read_users=["blocked-1"],
        group_acls=[
            GroupResourceAcl(
                group_id="group-1",
                default_readable=True,
                readable_users=["group-user"],
                excluded_read_users=["group-blocked"],
            )
        ],
    )


class _AuthoritativeReader:
    async def get_resource_acl(self, resource_id):
        return _acl()


class _LocalStore:
    def __init__(self, current=None) -> None:
        self.calls = []
        self.current = current or _acl()

    async def get_resource_acl(self, resource_id):
        return self.current if self.current.resource_id == resource_id else None

    async def save_if_newer(self, resource_acl):
        self.calls.append(resource_acl)
        return False

    async def get_resource_acls(self, resource_ids):
        return {self.current.resource_id: self.current}


class _BackendWriter:
    def __init__(self, *, fail=False) -> None:
        self.calls = []
        self.fail = fail

    async def synchronize(self, resource_acl):
        self.calls.append(resource_acl)
        if self.fail:
            raise RuntimeError("backend failed")


@pytest.mark.asyncio
async def test_refresher_retries_backends_even_when_local_revision_exists() -> None:
    local = _LocalStore()
    retrieval = _BackendWriter()
    graph = _BackendWriter()
    refresher = ResourceAclRefresher(
        authoritative_reader=_AuthoritativeReader(),
        local_store=local,
        retrieval_writer=retrieval,
        graph_writer=graph,
        graph_enabled=True,
    )

    await refresher.refresh("resource-1")

    assert local.calls == [_acl()]
    assert retrieval.calls == [_acl()]
    assert graph.calls == [_acl()]


@pytest.mark.asyncio
async def test_refresher_propagates_partial_backend_failure() -> None:
    refresher = ResourceAclRefresher(
        authoritative_reader=_AuthoritativeReader(),
        local_store=_LocalStore(),
        retrieval_writer=_BackendWriter(fail=True),
        graph_writer=_BackendWriter(),
        graph_enabled=True,
    )

    with pytest.raises(ExceptionGroup):
        await refresher.refresh("resource-1")


@pytest.mark.asyncio
async def test_refresher_pushes_current_acl_when_event_is_older_than_local() -> None:
    newer = _acl()
    newer.acl_revision = 8
    retrieval = _BackendWriter()
    graph = _BackendWriter()
    refresher = ResourceAclRefresher(
        authoritative_reader=_AuthoritativeReader(),
        local_store=_LocalStore(current=newer),
        retrieval_writer=retrieval,
        graph_writer=graph,
        graph_enabled=True,
    )

    await refresher.refresh("resource-1")

    assert retrieval.calls == [newer]
    assert graph.calls == [newer]


@pytest.mark.asyncio
async def test_refresher_skips_graph_acl_when_graph_is_disabled() -> None:
    retrieval = _BackendWriter()
    graph = _BackendWriter()
    refresher = ResourceAclRefresher(
        authoritative_reader=_AuthoritativeReader(),
        local_store=_LocalStore(),
        retrieval_writer=retrieval,
        graph_writer=graph,
        graph_enabled=False,
    )

    await refresher.refresh("resource-1")

    assert retrieval.calls == [_acl()]
    assert graph.calls == []


class _Qdrant:
    def __init__(self) -> None:
        self.calls = []

    async def collection_exists(self, collection_name):
        return True

    async def set_payload(self, **kwargs):
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_qdrant_writer_updates_acl_payload_monotonically() -> None:
    client = _Qdrant()
    writer = QdrantRetrievalAclWriter(
        client=client,
        collection_name="retrieval",
    )

    await writer.synchronize(_acl())

    call = client.calls[0]
    assert call["payload"]["acl_revision"] == 7
    assert call["payload"]["group_acls"][0]["group_id"] == "group-1"
    conditions = call["points"].must
    assert conditions[0].match.value == "resource-1"
    assert conditions[1].range.lte == 7


@dataclass
class _Result:
    records: list[dict]


class _Neo4jDriver:
    def __init__(self) -> None:
        self.calls = []

    async def execute_query(self, query, **parameters):
        self.calls.append((query, parameters))
        return _Result([])


class _Cache:
    async def bump_epoch(self):
        return "1"


@pytest.mark.asyncio
async def test_neo4j_writer_persists_group_acl_and_revision_guard() -> None:
    driver = _Neo4jDriver()
    writer = Neo4jGraphAclWriter(
        driver=driver,
        database="rag-v2",
        subgraph_cache=_Cache(),
    )

    await writer.synchronize(_acl())

    query, parameters = driver.calls[0]
    assert "resource.acl_revision <= $acl_revision" in query
    assert "RAG_V2_HAS_GROUP_ACL" in query
    assert parameters["acl_revision"] == 7
    assert parameters["group_acls"][0]["acl_id"] == "resource-1:group-1"


def test_neo4j_acl_predicate_uses_same_owner_user_and_group_semantics() -> None:
    predicate, parameters = _acl_predicate(
        PermissionScope(user_id="user-1"),
        resource_alias="resource",
    )

    assert "resource.owner_id = $acl_user_id" in predicate
    assert "$acl_user_id IN coalesce(resource.readable_users, [])" in predicate
    assert "RAG_V2_HAS_GROUP_ACL" in predicate
    assert parameters["acl_user_id"] == "user-1"

"""以真实发布、授权回查及通用用例验证雇佣扩展；模型与投影 I/O 使用替身。"""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI
from pydantic import ValidationError

from rag.domain.models.acl import PermissionScope, ResourceAcl
from rag.application.common.snapshot import ResourceSnapshot
from rag.application.graph import GraphTraversalResult
from rag.application.publication import DocumentReadyEvent
from rag.domain.models.publication import PublicationAction
from rag.container import Container
from rag.core.filters import FilterOperator
from rag.extensions import register_extensions
from rag.extensions.employment import EmploymentContainer
from rag.extensions.employment.api import (
    ContractSearch,
    EmploymentTraversal,
    GraphSearch,
)
from rag.extensions.employment.metadata import (
    EmploymentMetadataTransformer,
    employment_projection,
)
from rag.application.publication.ingestion.chunkers.markdown import MarkdownChunker
from rag.application.publication.ingestion import GraphExtraction
from rag.domain.storage.graph import GraphDirection
from rag.domain.storage.vector import VectorMatch

PACKAGE = Path(__file__).resolve().parents[2] / "src/rag/extensions/employment"


class MemoryVectors:
    """实现本例使用的来源/业务过滤；通用应用只核对授权和记录身份。"""

    def __init__(self, collection):
        self.collection = collection
        self.records = {}
        self.upsert = AsyncMock(side_effect=self._upsert)
        self.query = AsyncMock(side_effect=self._query)
        self.delete = AsyncMock(side_effect=self._delete)

    async def _upsert(self, records):
        self.records.update((record.record_id, record) for record in records)

    async def _query(self, request):
        records = list(self.records.values())
        if request.filters is not None:
            for field in request.filters.filters:
                if field.operator is FilterOperator.EQ:
                    records = [
                        record
                        for record in records
                        if record.metadata.get(field.key) == field.value
                    ]
                elif field.operator is FilterOperator.GTE:
                    records = [
                        record
                        for record in records
                        if record.metadata[field.key] >= field.value
                    ]
                elif field.operator is FilterOperator.LTE:
                    records = [
                        record
                        for record in records
                        if record.metadata[field.key] <= field.value
                    ]
                else:
                    raise NotImplementedError(field.operator)
        return [
            VectorMatch(record.record_id, 1.0, record.metadata, record.embedding)
            for record in records[: request.top_k]
        ]

    async def _delete(self, filters):
        resource = filters.filters[0].value
        self.records = {
            key: record
            for key, record in self.records.items()
            if record.metadata["resource_id"] != resource
        }


class MemoryGraph:
    def __init__(self):
        self.sources = {}
        self.replace_sources = AsyncMock(side_effect=self._replace)
        self.get_nodes = AsyncMock(side_effect=self._nodes)
        self.get_relations = AsyncMock(side_effect=self._relations)
        self.get_neighbors = AsyncMock(side_effect=self._neighbors)
        self.delete_sources = AsyncMock(side_effect=self._delete)

    async def _replace(self, projections):
        self.sources.update(
            (projection.chunk_id, projection) for projection in projections
        )

    async def _nodes(self, ids, *, filters=None):
        return sorted(
            [
                node
                for source in self.sources.values()
                if filters is None
                   or all(
                source.metadata[field.key] == field.value
                for field in filters.filters
            )
                for node in source.nodes
                if node.id in ids
            ],
            key=lambda node: (ids.index(node.id), node.source_id),
        )

    async def _relations(self, ids, *, filters=None):
        return sorted(
            [
                edge
                for source in self.sources.values()
                if filters is None
                   or all(
                source.metadata[field.key] == field.value
                for field in filters.filters
            )
                for edge in source.edges
                if edge.id in ids
            ],
            key=lambda edge: (ids.index(edge.id), edge.source_ids),
        )

    async def _neighbors(
            self, ids, *, direction, relation_types=None, limit=100, filters=None
    ):
        edges = [
            edge
            for source in self.sources.values()
            if filters is None
               or all(
                source.metadata[field.key] == field.value for field in filters.filters
            )
            for edge in source.edges
            if (
                       direction in (GraphDirection.OUTGOING, GraphDirection.BOTH)
                       and edge.source_id in ids
                       or direction in (GraphDirection.INCOMING, GraphDirection.BOTH)
                       and edge.target_id in ids
               )
               and (relation_types is None or edge.relation_type in relation_types)
        ]
        selected = sorted({edge.id for edge in edges})[:limit]
        return sorted(
            [edge for edge in edges if edge.id in selected],
            key=lambda edge: (edge.id, edge.source_ids),
        )

    async def _delete(self, filters):
        self.sources = {
            key: source
            for key, source in self.sources.items()
            if source.metadata["resource_id"] != filters.filters[0].value
        }


@pytest.fixture
async def employment_env(
        platform_settings, storage, publication_store, read_acl_repository, read_acls
):
    settings = type(platform_settings)(
        **(
                platform_settings.model_dump()
                | {
                    "EXTENSIONS": ["employment"],
                    "ENABLE_GRAPH_STORAGE": True,
                    "NEO4J_URI": "neo4j://localhost:7687",
                    "NEO4J_USERNAME": "test",
                    "NEO4J_PASSWORD": "test",
                    "INSTRUCTION_BASE_URL": "http://localhost/v1",
                    "INSTRUCTION_API_KEY": "test",
                    "INSTRUCTION_MODEL": "test",
                    "FROM_SOURCE_SECRET": "test-gateway",
                }
        )
    )
    platform = Container(settings=settings)
    embedding = SimpleNamespace(
        dimensions=2,
        embed=AsyncMock(side_effect=lambda *, texts: [[1.0, 0.0] for _ in texts]),
    )
    instruction = SimpleNamespace(model="test", structured_complete=AsyncMock())
    graph = MemoryGraph()
    ordinary, entities, relations, dedicated = [
        MemoryVectors(name)
        for name in ("ordinary", "entities", "relations", "employment")
    ]
    for name, dependency in {
        "content_storage": storage,
        "publication_store": publication_store,
        "acl_repository": read_acl_repository,
        "embedding_client": embedding,
        "instruction_client": instruction,
        "graph_storage": graph,
        "vector_storage": ordinary,
        "entity_vector_storage": entities,
        "relationship_vector_storage": relations,
        "chunk_reranker": None,
    }.items():
        getattr(platform, name).override(providers.Object(dependency))
    extension = EmploymentContainer(platform=platform)
    extension.storage.override(providers.Object(dedicated))
    # 替换实际构造器依赖；注册逻辑本身仍执行同一个 manifest/GC 合同。
    import rag.extensions as registry

    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(registry._BUILDERS, "employment", lambda *, platform: extension)
        collector = platform.garbage_collector()
        registered = register_extensions(platform)
    publisher = platform.publication()
    events = [
        DocumentReadyEvent.model_validate(json.loads(line))
        for line in (PACKAGE / "contracts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    for event, department in zip(
            events, ("研发部", "产品部", "研发部", "研发部"), strict=True
    ):
        data = event.metadata
        instruction.structured_complete.return_value = GraphExtraction.model_validate(
            {
                "entities": [
                    {
                        "name": data["employee"],
                        "category": "Person",
                        "description": "雇员独立来源描述",
                    },
                    {
                        "name": department,
                        "category": "Department",
                        "description": "部门独立来源描述",
                    },
                ],
                "relationships": [
                    {
                        "source": {"name": data["employee"], "category": "Person"},
                        "target": {
                            "name": department,
                            "category": "Department",
                        },
                        "relation_type": "in_department",
                        "description": "部门隶属关系",
                        "keywords": ["部门"],
                    }
                ],
            }
        )
        assert await publisher.publish(event.to_document()) is PublicationAction.APPLIED
        read_acls[event.resource_id] = ResourceAcl(event.resource_id, 1, "reader")
    chunks = list(await storage.get_chunks(list(dedicated.records)))
    return SimpleNamespace(
        platform=platform,
        extension=extension,
        registered=registered,
        collector=collector,
        publisher=publisher,
        embedding=embedding,
        instruction=instruction,
        graph=graph,
        ordinary=ordinary,
        entities=entities,
        relations=relations,
        dedicated=dedicated,
        events=events,
        chunks=chunks,
        storage=storage,
        publication_store=publication_store,
        acls=read_acls,
        acl_repository=read_acl_repository,
        snapshot=lambda: ResourceSnapshot(PermissionScope("reader")),
    )


def test_lazy_package_local_ontology(monkeypatch, tmp_path):
    extension = EmploymentContainer(platform=Container())
    monkeypatch.chdir(tmp_path)
    ontology = extension.ontology()
    assert ontology.domain == "employment"
    assert ontology.relation_specs["in_department"].allowed_targets == ["Department"]


async def test_publish_all_indexes_metadata_and_seeds(employment_env):
    env = employment_env
    assert len(env.ordinary.records) == len(env.dedicated.records) == 4
    assert len(env.entities.records) == 8 and len(env.relations.records) == 4
    assert len(env.graph.sources) == 4
    for chunk in env.chunks:
        payload = env.dedicated.records[chunk.chunk_id].metadata
        assert payload == {
            **employment_projection(chunk),
            "resource_id": chunk.resource_id,
            "content_revision": chunk.content_revision,
        }
        assert isinstance(payload["year"], int)
        event = next(
            event for event in env.events if event.resource_id == chunk.resource_id
        )
        assert {
                   key: chunk.metadata[key] for key in ("employee", "start_date")
               } == event.metadata
        stored_document = (
            await env.storage.get_documents(
                [(chunk.resource_id, chunk.content_revision)]
            )
        )[0]
        assert stored_document.metadata == event.metadata
        assert "year" not in chunk.metadata and "employment" not in chunk.metadata
    hits = await env.extension.tools().search_contracts(
        "研发合同",
        snapshot=env.snapshot(),
        year_from=2022,
        year_to=2024,
        employee="陈晨",
    )
    assert (
            len(hits) == 1
            and hits[0].metadata["employee"] == "陈晨"
            and hits[0].metadata["start_date"].startswith("2022-")
    )
    assert hits[0].text == env.chunks[2].raw_text
    assert hits[0].seed_entities[0].id == "employment:Person:陈晨"
    assert hits[0].seed_entities[0].relations[0].relation_type == "in_department"
    request = env.dedicated.query.call_args.args[0]
    assert [condition.key for condition in request.filters.filters] == [
        "year",
        "year",
        "employee",
    ]
    assert env.platform.pipeline().transformers == []
    before = env.instruction.structured_complete.await_count
    assert (
            await env.publisher.publish(env.events[0].to_document())
            is PublicationAction.ALREADY_APPLIED
    )
    assert env.instruction.structured_complete.await_count == before


async def test_metadata_validation_preserves_original_values_and_chunk_isolation(
        document,
):
    original = {"employee": "  LI   Ming  ", "start_date": "2020-03-01"}
    document = replace(
        document,
        text="# 内部劳动合同\n\n"
             + "员工隶属于研发部，负责软件研发和项目维护。\n\n" * 40,
        metadata=original,
    )
    chunks = await MarkdownChunker(40, 70).split(document)
    assert len(chunks) > 1
    before = deepcopy(chunks)
    transformer = EmploymentMetadataTransformer()
    assert await transformer.transform(chunks, document=document) is chunks
    assert chunks == before
    assert all(chunk.metadata == original for chunk in chunks)
    assert employment_projection(chunks[0]) == {"employee": "LI   Ming", "year": 2020}
    assert chunks == before  # 业务投影也不把校验后的名称/year 回写到事实。
    chunks[0].metadata["employee"] = "局部修改"
    assert document.metadata == original
    assert all(chunk.metadata == original for chunk in chunks[1:])


async def test_empty_document_still_validates_metadata_without_rewriting(document):
    transformer = EmploymentMetadataTransformer()
    valid = replace(document, metadata={"employee": "李明", "start_date": "2020-03-01"})
    chunks = []
    assert await transformer.transform(chunks, document=valid) is chunks
    with pytest.raises(ValidationError):
        await transformer.transform([], document=replace(valid, metadata={}))


@pytest.mark.parametrize(
    "change", ["identity", "vector-revision", "hidden", "destroyed", "revision"]
)
async def test_authoritative_prepare_rejects_stale_or_hidden(employment_env, change):
    env = employment_env
    chunk = env.chunks[0]
    env.dedicated.records = {chunk.chunk_id: env.dedicated.records[chunk.chunk_id]}
    if change in ("identity", "vector-revision"):
        record = env.dedicated.records[chunk.chunk_id]
        env.dedicated.records[chunk.chunk_id] = replace(
            record,
            metadata={
                **record.metadata,
                "resource_id" if change == "identity" else "content_revision": "wrong",
            },
        )
    elif change == "hidden":
        env.acls.pop(chunk.resource_id)
    elif change == "destroyed":
        await env.publication_store.destroy_resources([chunk.resource_id])
    else:
        new = DocumentReadyEvent.model_validate(
            {"resourceId": chunk.resource_id, "version": 2, "content": "新合同"}
        ).to_document()
        from rag.domain.models import ContentRevision

        rev = ContentRevision.create(
            resource_id=new.resource_id,
            document_version=new.document_version,
            text=new.text,
        )
        await env.publication_store.stage_revision(rev)
        await env.publication_store.apply_revision(rev)
    assert (
            await env.extension.tools().search_contracts("合同", snapshot=env.snapshot())
            == []
    )


async def test_business_changes_use_new_version_and_rebuild_projection(employment_env):
    env = employment_env
    original = env.events[0]
    metadata = {
        **original.metadata,
        "start_date": "2023-03-01",
    }
    unchanged_version = original.model_copy(update={"metadata": metadata})
    assert (
            await env.publisher.publish(unchanged_version.to_document())
            is PublicationAction.ALREADY_APPLIED
    )
    rows = await env.extension.tools().search_contracts(
        "合同", snapshot=env.snapshot(), year_from=2023, year_to=2023
    )
    assert rows == []
    new_version = unchanged_version.model_copy(update={"document_version": 2})
    assert (
            await env.publisher.publish(new_version.to_document())
            is PublicationAction.APPLIED
    )
    rows = await env.extension.tools().search_contracts(
        "合同", snapshot=env.snapshot(), year_from=2023, year_to=2023
    )
    assert len(rows) == 1 and rows[0].metadata["start_date"] == "2023-03-01"
    # 当前 revision 的已有 Chunk 可通过公开 Indexer 重建，业务用例无需专用准备。
    chunks = await env.storage.get_revision_chunks(
        resource_id=original.resource_id,
        content_revision=new_version.to_document().content_revision,
    )
    record_id = chunks[0].chunk_id
    env.dedicated.records.pop(record_id)
    await env.extension.contract_indexer().index(chunks)
    assert record_id in env.dedicated.records


@pytest.mark.parametrize("mode", ["local", "global", "hybrid"])
async def test_generic_graph_wrapping_and_explicit_two_hops(employment_env, mode):
    env = employment_env
    tools = env.extension.tools()
    env.embedding.embed.reset_mock()
    hits = await tools.search_graph("任职", snapshot=env.snapshot(), mode=mode)
    assert hits.chunks and hits.relationships
    env.embedding.embed.assert_awaited_once()
    first = await tools.follow(["employment:Person:李明"], snapshot=env.snapshot())
    assert len(first.relationships) == 2
    second = await tools.follow(
        ["employment:Department:研发部"],
        snapshot=env.snapshot(),
        direction=GraphDirection.INCOMING,
    )
    assert len(second.relationships) == 3
    env.acls.pop("employment-contract-003")
    second = await tools.follow(
        ["employment:Department:研发部"],
        snapshot=env.snapshot(),
        direction=GraphDirection.BOTH,
    )
    assert len(second.relationships) == 2
    assert all(
        source.chunk_id
        not in {
            chunk.chunk_id
            for chunk in env.chunks
            if chunk.resource_id == "employment-contract-003"
        }
        for node in second.entities
        for source in node.sources
    )
    assert (
            await tools.follow(["employment:Person:陈晨"], snapshot=env.snapshot())
            == GraphTraversalResult()
    )


async def test_registered_extra_collection_is_cleaned_with_shared_gc(employment_env):
    env = employment_env
    assert env.collector._vector_writers is env.platform.vector_storages()
    assert env.dedicated in env.collector._vector_writers
    env.acl_repository.delete = AsyncMock(
        side_effect=lambda resource: env.acls.pop(resource, None)
    )
    await env.publication_store.destroy_resources(["employment-contract-001"])
    report = await env.collector.collect()
    assert report.cleaned == 1 and not report.errors
    assert len(env.dedicated.records) == len(env.ordinary.records) == 3
    assert len(env.entities.records) == 6 and len(env.graph.sources) == 3
    env.dedicated.delete.assert_awaited_once()
    assert (await env.publication_store.get_states(["employment-contract-001"]))[
        0
    ].destroyed


async def test_specialized_index_failure_blocks_apply_and_retry_recovers(
        employment_env,
):
    env = employment_env
    event = env.events[0].model_copy(update={"document_version": 2})
    env.dedicated.upsert.side_effect = OSError("dedicated index unavailable")
    with pytest.raises(OSError):
        await env.publisher.publish(event.to_document())
    assert (await env.publication_store.get_states([event.resource_id]))[
               0
           ].applied_revision.document_version == 1
    env.dedicated.upsert.side_effect = env.dedicated._upsert
    assert await env.publisher.publish(event.to_document()) is PublicationAction.APPLIED


async def test_business_validation_precedes_index_writes(employment_env):
    env = employment_env
    event = env.events[0].model_copy(update={"document_version": 2, "metadata": {}})
    calls = env.ordinary.upsert.await_count
    with pytest.raises(ValidationError):
        await env.publisher.publish(event.to_document())
    assert env.ordinary.upsert.await_count == calls


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": " "},
        {"query": "q", "year_from": 2025, "year_to": 2020},
        {"query": "q", "candidate_limit": 1},
    ],
)
async def test_invalid_business_query_has_no_io(employment_env, arguments):
    env = employment_env
    env.dedicated.query.reset_mock()
    env.embedding.embed.reset_mock()
    with pytest.raises(ValueError):
        ContractSearch(**arguments)
    env.dedicated.query.assert_not_awaited()
    env.embedding.embed.assert_not_awaited()


async def test_api_mount_trusted_identity_and_full_sources(employment_env):
    env = employment_env
    api = env.registered["employment"].api
    parent = FastAPI()
    parent.mount("/rag/extensions/employment", api)
    headers = {"X-From-Source": "test-gateway", "X-User-Id": "reader"}
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=parent), base_url="http://test"
    ) as client:
        path = "/rag/extensions/employment/contracts/search"
        assert (await client.post(path, json={"query": "合同"})).status_code == 404
        assert (
                   await client.post(path, json={"query": "合同"}, headers=headers)
               ).status_code == 503
        api.state.ready = True
        assert (
                   await client.post(
                       path, json={"query": "合同"}, headers={"X-From-Source": "test-gateway"}
                   )
               ).status_code == 401
        response = await client.post(
            path, json={"query": "合同", "year_from": 2024}, headers=headers
        )
        assert response.status_code == 200 and [
            hit["metadata"]["start_date"][:4] for hit in response.json()
        ] == ["2024", "2025"]
        assert response.json()[0]["seed_entities"]
        graph_response = await client.post(
            "/rag/extensions/employment/graph/search",
            json={"query": "合同", "mode": "local", "top_k": 1},
            headers=headers,
        )
        assert graph_response.status_code == 200
        graph_result = graph_response.json()
        assert set(graph_result) == {"entities", "relationships", "chunks"}
        assert len(graph_result["chunks"]) == 1
        assert graph_result["chunks"][0]["seed_entities"][0]["relations"]
        employee = await client.post(
            path,
            json={"query": "合同", "employee": " 陈晨 "},
            headers=headers,
        )
        assert employee.status_code == 200
        assert [hit["metadata"]["employee"] for hit in employee.json()] == ["陈晨"]
        assert (
                   await client.post(
                       path, json={"query": "合同", "user_id": "owner"}, headers=headers
                   )
               ).status_code == 422
        hidden = await client.post(
            path, json={"query": "合同"}, headers={**headers, "X-User-Id": "unknown"}
        )
        assert hidden.status_code == 200 and hidden.json() == []
        traversal = await client.post(
            "/rag/extensions/employment/graph/traverse",
            json={"node_ids": ["employment:Person:李明"]},
            headers=headers,
        )
        assert (
                traversal.status_code == 200 and len(traversal.json()["relationships"]) == 2
        )
        outline = await client.get(
            "/rag/extensions/employment/contracts/employment-contract-001/outline",
            headers=headers,
        )
        assert outline.status_code == 200


async def test_api_preserves_storage_failure(employment_env):
    env = employment_env
    api = env.registered["employment"].api
    api.state.ready = True
    env.dedicated.query.side_effect = OSError("vector database unavailable")
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api), base_url="http://test"
    ) as client:
        with pytest.raises(OSError):
            await client.post(
                "/contracts/search",
                json={"query": "合同"},
                headers={"X-From-Source": "test-gateway", "X-User-Id": "reader"},
            )


async def test_authorization_precedes_rerank_and_empty_skips_model(employment_env):
    env = employment_env
    model = SimpleNamespace(
        rerank=AsyncMock(side_effect=OSError("reranker unavailable"))
    )
    env.extension.contracts()._reranker = model
    env.acls.pop("employment-contract-004")
    hits = await env.extension.tools().search_contracts(
        "合同", snapshot=env.snapshot(), year_from=2024
    )
    assert [hit.metadata["start_date"][:4] for hit in hits] == ["2024"]
    assert [hit.rank for hit in hits] == [1]
    materials = model.rerank.call_args.kwargs["candidates"]
    assert [item.payload.chunk.resource_id for item in materials] == [
        "employment-contract-002"
    ]
    env.acls.clear()
    model.rerank.reset_mock()
    assert (
            await env.extension.tools().search_contracts("合同", snapshot=env.snapshot())
            == []
    )
    model.rerank.assert_not_awaited()


async def test_graph_tools_select_ontology_without_requiring_business_metadata(
        employment_env,
):
    env = employment_env
    chunk = env.chunks[2]
    chunk.metadata.pop("employee")
    chunk.metadata.pop("start_date")
    await env.storage.upsert_chunks([chunk])
    groups = await env.extension.tools().follow(
        ["employment:Department:研发部"],
        snapshot=env.snapshot(),
        direction=GraphDirection.BOTH,
    )
    assert len(groups.relationships) == 3
    assert chunk.chunk_id in {
        source.chunk_id for node in groups.entities for source in node.sources
    }
    person_topology = await env.extension.tools().follow(
        ["employment:Person:陈晨"], snapshot=env.snapshot()
    )
    assert person_topology.entities


async def test_graph_tools_keep_orphan_seed_and_do_not_reload_evidence(employment_env):
    env = employment_env
    for chunk in env.chunks:
        chunk.metadata["relationships"] = []
    await env.storage.upsert_chunks(env.chunks)
    reader = env.platform.snapshot_reader()
    reader.read_chunks = AsyncMock(wraps=reader.read_chunks)
    groups = await env.extension.tools().follow(
        ["employment:Person:陈晨"], snapshot=env.snapshot()
    )
    assert groups.entities and groups.relationships == []
    assert [node.id for node in groups.entities] == ["employment:Person:陈晨"]
    reader.read_chunks.assert_awaited_once()
    hits = await env.extension.tools().search_graph(
        "任职", snapshot=env.snapshot(), mode="local"
    )
    assert hits.chunks and hits.relationships == []
    request = env.entities.query.call_args.args[0]
    assert [(field.key, field.value) for field in request.filters.filters] == [
        ("ontology_name", "employment")
    ]


async def test_tools_select_namespace_from_facts_without_parsing_id(employment_env):
    from dataclasses import asdict

    from rag.application.publication.ingestion import GraphIndexer

    env = employment_env
    chunk = env.chunks[2]
    ontology = replace(env.extension.ontology(), domain="other")
    person = ontology.create_node(
        name="陈晨", category="Person", source_id=chunk.chunk_id
    )
    company = ontology.create_node(
        name="研发部", category="Department", source_id=chunk.chunk_id
    )
    nodes = {node.id: node for node in (person, company)}
    edge = ontology.create_edge(
        nodes=nodes,
        source_id=person.id,
        target_id=company.id,
        relation_type="in_department",
        chunk_id=chunk.chunk_id,
    )
    chunk.metadata.update(
        entities=[asdict(node) for node in nodes.values()], relationships=[asdict(edge)]
    )
    await env.storage.upsert_chunks([chunk])
    await GraphIndexer(ontology=ontology, writer=env.graph).index([chunk])
    # 通用 traversal 允许其他本体，业务工具才对自己的证据命名空间做选择。
    foreign_topology = await env.platform.graph_traversal().traverse(
        [person.id], snapshot=env.snapshot()
    )
    assert foreign_topology.entities
    assert (
            await env.extension.tools().follow([person.id], snapshot=env.snapshot())
            == GraphTraversalResult()
    )


@pytest.mark.parametrize(
    "model,payload",
    [
        (GraphSearch, {"query": "query", "top_k": 0}),
        (GraphSearch, {"query": " "}),
        (EmploymentTraversal, {"node_ids": [" "]}),
    ],
)
def test_graph_input_constraints_belong_to_api_models(model, payload):
    # 公开输入由 Pydantic 拒绝，应用内部不重复实现种子与预算校验。
    with pytest.raises(ValidationError):
        model.model_validate(payload)


async def test_api_openapi_exposes_tool_result_contracts(employment_env):
    schemas = employment_env.registered["employment"].api.openapi()["components"][
        "schemas"
    ]
    assert "metadata" in schemas["ChunkHit"]["properties"]
    assert "seed_entities" in schemas["ChunkHit"]["properties"]
    assert "employee" in schemas["ContractSearch"]["properties"]
    assert "employer" not in schemas["ContractSearch"]["properties"]
    assert set(schemas["GraphNodeReference"]["properties"]) == {"id", "relations"}
    assert set(schemas["GraphTraversalResult"]["properties"]) == {
        "entities",
        "relationships",
    }
    assert "GraphEvidence" not in schemas and "GraphSourceChunk" not in schemas
    assert set(schemas["GraphEntity"]["properties"]) == {"id", "sources"}
    assert set(schemas["GraphEntitySource"]["properties"]) == {
        "chunk_id",
        "description",
    }
    assert set(schemas["GraphRelationship"]["properties"]) == {"id", "sources"}
    assert set(schemas["GraphRelationshipSource"]["properties"]) == {
        "chunk_id",
        "description",
        "keywords",
        "metadata",
    }
    assert "GraphNode" not in schemas and "GraphEdge" not in schemas
    assert "embedding" not in schemas["GraphRetrievalResult"]["properties"]
    assert set(schemas["GraphRetrievalResult"]["properties"]) == {
        "entities",
        "relationships",
        "chunks",
    }
    assert {
               item.get("type")
               for item in schemas["GraphNodeReference"]["properties"]["relations"]["anyOf"]
           } == {"array", "null"}
    assert "ResourceSnapshot" not in schemas


async def test_contract_search_without_graph_reports_unknown_connections(
        employment_env,
):
    env = employment_env
    env.platform.graph_storage.override(providers.Object(None))
    # 注册夹具已构造过单例，重装配合同检索以验证无图库的注入路径。
    env.extension.contracts.reset()
    contracts = env.extension.contracts()
    assert contracts._graph_reader is None
    hits = await contracts.retrieve("合同", snapshot=env.snapshot(), mode="dense")
    assert hits
    assert all(seed.relations is None for hit in hits for seed in hit.seed_entities)
    env.graph.get_neighbors.assert_not_awaited()

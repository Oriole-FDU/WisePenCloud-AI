"""图事实 repair 的显式范围、写前复核、部分写入和客户端清理。"""

import asyncio
import importlib.util
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from rag.application.publication import DocumentReadyEvent
from rag.core.schema import ContentRevision
from rag.ingestion.chunkers import MarkdownChunker
from rag.ingestion.indexers import ChunkFactsIndexer, GraphIndexer
from rag.ingestion.transformers import GraphExtractor
from rag.ingestion.transformers.graph import GraphExtraction
from rag.storage.vector import IndexableField, IndexKind


@pytest.fixture(scope="module")
def repair():
    path = Path(__file__).resolve().parents[2] / "scripts" / "repair_graph.py"
    spec = importlib.util.spec_from_file_location("_test_rag_repair_graph", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def flow(storage, publication_store, graph_ontology):
    client = SimpleNamespace(
        model="model",
        structured_complete=AsyncMock(
            return_value=GraphExtraction(entities=[], relationships=[])
        ),
    )
    return SimpleNamespace(
        client=client,
        ontology=graph_ontology,
        publication=publication_store,
        content=storage,
        extractor=GraphExtractor(ontology=graph_ontology, client=client),
        facts=ChunkFactsIndexer(storage),
    )


async def prepare(
        flow, resource_id="resource", *, version=1, applied=True, text="# 标题\n\n正文"
):
    document = DocumentReadyEvent.model_validate(
        {"resourceId": resource_id, "version": version, "content": text}
    ).to_document()
    revision = ContentRevision.create(
        resource_id=resource_id, document_version=version, text=text
    )
    await flow.content.upsert_documents([document])
    chunks = await MarkdownChunker().split(document)
    for chunk in chunks:
        chunk.metadata.update(
            contextual_context="保留上下文",
            context_status="ok",
            context_version="context-version",
            custom="保留",
        )
    await flow.content.upsert_chunks(chunks)
    await flow.publication.stage_revision(revision)
    if applied:
        await flow.publication.apply_revision(revision)
    return document, chunks


async def run_repair(repair, flow, resources):
    return await repair.repair_resources(
        resources,
        publication=flow.publication,
        content=flow.content,
        extractor=flow.extractor,
        facts=flow.facts,
    )


async def test_explicit_applied_scope_incremental_and_force(repair, flow):
    document, chunks = await prepare(flow)
    before_texts = [chunk.retrieval_text for chunk in chunks]
    await prepare(flow, "staged", applied=False)
    await prepare(flow, "destroyed")
    await flow.publication.destroy_resources(["destroyed"])
    await prepare(flow, "unselected")
    before_state = (await flow.publication.get_states(["resource"]))[0]
    first = await run_repair(
        repair, flow, ["missing", "staged", "destroyed", "resource", "resource"]
    )
    assert (first.repaired, first.skipped, first.document_errors, first.exit_code) == (
        1,
        3,
        0,
        0,
    )
    restored = await flow.content.get_revision_chunks(
        resource_id="resource", content_revision=document.content_revision
    )
    assert [chunk.retrieval_text for chunk in restored] == before_texts
    assert all(
        chunk.metadata["custom"] == "保留"
        and chunk.metadata["context_version"] == "context-version"
        for chunk in restored
    )
    assert all(
        chunk.metadata["entities"] == [] and chunk.metadata["relationships"] == []
        for chunk in restored
    )
    flow.client.structured_complete.reset_mock()
    assert (await run_repair(repair, flow, ["resource"])).repaired == 1
    flow.client.structured_complete.assert_not_awaited()
    flow.extractor = GraphExtractor(
        ontology=flow.ontology, client=flow.client, reuse_existing=False
    )
    assert (await run_repair(repair, flow, ["resource"])).repaired == 1
    assert flow.client.structured_complete.await_count == len(chunks)
    assert (await flow.publication.get_states(["resource"]))[0] == before_state


@pytest.mark.parametrize("change", ["new-applied", "staged", "destroyed"])
async def test_state_change_skips_writes(repair, flow, change):
    await prepare(flow)

    async def generate(**kwargs):
        if change == "destroyed":
            await flow.publication.destroy_resources(["resource"])
        else:
            newer = ContentRevision.create(
                resource_id="resource", document_version=2, text="新版"
            )
            await flow.publication.stage_revision(newer)
            if change == "new-applied":
                await flow.publication.apply_revision(newer)
        return GraphExtraction(entities=[], relationships=[])

    flow.client.structured_complete.side_effect = generate
    flow.facts.index = AsyncMock()
    report = await run_repair(repair, flow, ["resource"])
    assert (report.skipped, report.repaired, report.exit_code) == (1, 0, 0)
    flow.facts.index.assert_not_awaited()


@pytest.mark.parametrize(
    "damage",
    [
        "document-text",
        "document-revision",
        "missing-document",
        "chunk-resource",
        "chunk-revision",
    ],
)
async def test_identity_errors_precede_model_and_writes(repair, flow, damage):
    document, chunks = await prepare(flow)
    if damage == "document-text":
        document.text = "身份被破坏"
        await flow.content.upsert_documents([document])
    elif damage == "document-revision":
        document.content_revision = "wrong"
        flow.content.get_documents = AsyncMock(return_value=[document])
    elif damage == "missing-document":
        flow.content.get_documents = AsyncMock(return_value=[])
    else:
        field = "resource_id" if damage == "chunk-resource" else "content_revision"
        flow.content.get_revision_chunks = AsyncMock(
            return_value=[replace(chunks[0], **{field: "wrong"})]
        )
    flow.facts.index = AsyncMock()
    report = await run_repair(repair, flow, ["resource"])
    assert (report.document_errors, report.repaired, report.exit_code) == (1, 0, 1)
    flow.client.structured_complete.assert_not_awaited()
    flow.facts.index.assert_not_awaited()


async def test_model_failure_keeps_old_facts_and_continues_other_resource(repair, flow):
    document, chunks = await prepare(flow, "bad", text="失败材料")
    for chunk in chunks:
        chunk.metadata.update(
            entities=[{"old": True}], relationships=[], graph_version="old"
        )
    await flow.content.upsert_chunks(chunks)
    await prepare(flow, "good", text="成功材料")

    async def generate(**kwargs):
        if "失败材料" in kwargs["messages"][1]["content"]:
            raise RuntimeError("provider failure")
        return GraphExtraction(entities=[], relationships=[])

    flow.client.structured_complete.side_effect = generate
    report = await run_repair(repair, flow, ["bad", "good"])
    assert (report.document_errors, report.repaired, report.exit_code) == (1, 1, 1)
    restored = await flow.content.get_revision_chunks(
        resource_id="bad", content_revision=document.content_revision
    )
    assert restored == chunks


async def test_partial_write_then_retry_reuses_only_persisted_success(repair, flow):
    document, chunks = await prepare(flow, text="# 甲\n\n第一段\n\n# 乙\n\n第二段")
    original = flow.facts.index

    async def persist_first_then_fail(updated):
        # 在写入边界制造部分成功，不依赖 Mongo adapter 的批大小。
        await flow.content.upsert_chunks(updated[:1])
        raise OSError("partial write failure")

    flow.facts.index = AsyncMock(side_effect=persist_first_then_fail)
    report = await run_repair(repair, flow, ["resource"])
    assert (report.document_errors, report.repaired, report.exit_code) == (1, 0, 1)
    restored = await flow.content.get_revision_chunks(
        resource_id="resource", content_revision=document.content_revision
    )
    assert sum("graph_version" in chunk.metadata for chunk in restored) == 1
    flow.facts.index = original
    flow.client.structured_complete.reset_mock()
    assert (await run_repair(repair, flow, ["resource"])).repaired == 1
    assert flow.client.structured_complete.await_count == len(chunks) - 1
    restored = await flow.content.get_revision_chunks(
        resource_id="resource", content_revision=document.content_revision
    )
    assert all("graph_version" in chunk.metadata for chunk in restored)


async def test_zero_chunk_document_and_backend_error_continue(repair, flow):
    await prepare(flow, "empty", text="")
    original = flow.publication.get_states

    async def states(ids):
        if ids == ["backend-failure"]:
            raise OSError("database unavailable")
        return await original(ids)

    flow.publication.get_states = states
    report = await run_repair(repair, flow, ["backend-failure", "empty"])
    assert (report.document_errors, report.repaired, report.exit_code) == (1, 1, 1)
    flow.client.structured_complete.assert_not_awaited()


def config_arguments(tmp_path, graph_ontology):
    path = tmp_path / "ontology.json"
    path.write_text(
        json.dumps(asdict(graph_ontology), ensure_ascii=False), encoding="utf-8"
    )
    return [
        "--ontology-file",
        str(path),
        "--mongo-url",
        "mongodb://isolated",
        "--mongo-database",
        "isolated",
        "--instruction-base-url",
        "http://isolated/v1",
        "--instruction-api-key",
        "test",
        "--instruction-model",
        "model",
    ]


def test_cli_validates_files_scope_and_limits_before_connections(
        repair, tmp_path, graph_ontology
):
    arguments = config_arguments(tmp_path, graph_ontology)
    resources = tmp_path / "resources.txt"
    resources.write_text("b\na\n\n中文资源\n", encoding="utf-8")
    args = repair.parse_args(
        [
            *arguments,
            "--resource-id",
            "a",
            "--resource-file",
            str(resources),
            "--force",
            "--gleaning",
        ]
    )
    assert args.resource_ids == ["a", "b", "中文资源"]
    assert args.force and args.gleaning and args.ontology == graph_ontology
    for extra in (
            [],
            ["--resource-id", "a", "--max-input-tokens", "0"],
            ["--resource-file", str(tmp_path / "missing")],
    ):
        with pytest.raises(SystemExit):
            repair.parse_args([*arguments, *extra])
    (tmp_path / "ontology.json").write_text('{"domain": " "}', encoding="utf-8")
    with pytest.raises(SystemExit):
        repair.parse_args([*arguments, "--resource-id", "a"])


@pytest.mark.parametrize("fail_at", [None, "model-constructor", "repair"])
async def test_run_closes_clients_on_success_and_failure(
        repair, monkeypatch, tmp_path, graph_ontology, fail_at
):
    args = repair.parse_args(
        [
            *config_arguments(tmp_path, graph_ontology),
            "--resource-id",
            "resource",
            "--force",
        ]
    )
    mongo = DatabaseClient()
    instruction_api = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(repair, "AsyncMongoClient", Mock(return_value=mongo))
    monkeypatch.setattr(repair, "AsyncOpenAI", Mock(return_value=instruction_api))
    instruction = Mock(return_value=SimpleNamespace(model="model"))
    if fail_at == "model-constructor":
        instruction.side_effect = RuntimeError("setup failure")
    monkeypatch.setattr(repair, "InstructionClient", instruction)
    execute = AsyncMock(return_value=repair.RepairReport(repaired=1))
    if fail_at == "repair":
        execute.side_effect = OSError("repair failure")
    monkeypatch.setattr(repair, "repair_resources", execute)
    if fail_at:
        with pytest.raises((RuntimeError, OSError)):
            await repair.run(args)
    else:
        assert (await repair.run(args)).repaired == 1
    mongo.close.assert_awaited_once()
    instruction_api.close.assert_awaited_once()


class DatabaseClient:
    def __init__(self):
        self.close = AsyncMock()

    def __getitem__(self, name):
        return self


def test_main_reports_nonzero_and_setup_errors(
        repair, monkeypatch, tmp_path, graph_ontology, capsys
):
    arguments = [
        *config_arguments(tmp_path, graph_ontology),
        "--resource-id",
        "resource",
    ]
    monkeypatch.setattr(
        repair, "run", AsyncMock(return_value=repair.RepairReport(document_errors=1))
    )
    assert repair.main(arguments) == 1
    assert '"document_errors": 1' in capsys.readouterr().out
    monkeypatch.setattr(repair, "run", AsyncMock(side_effect=OSError("setup failure")))
    assert repair.main(arguments) == 1


async def test_graph_failure_keeps_facts_then_reuses_cache_and_rewrites_topology(
        repair, flow
):
    document, chunks = await prepare(flow)
    writer = SimpleNamespace(
        replace_sources=AsyncMock(side_effect=OSError("Neo4j unavailable"))
    )
    graph = GraphIndexer(ontology=flow.ontology, writer=writer)
    options = {
        "publication": flow.publication,
        "content": flow.content,
        "extractor": flow.extractor,
        "facts": flow.facts,
        "graph": graph,
    }
    first = await repair.repair_resources(["resource"], **options)
    assert (first.document_errors, first.repaired, first.exit_code) == (1, 0, 1)
    stored = await flow.content.get_revision_chunks(
        resource_id="resource", content_revision=document.content_revision
    )
    assert all(chunk.metadata.get("graph_version") for chunk in stored)
    flow.client.structured_complete.reset_mock()
    writer.replace_sources.side_effect = None
    second = await repair.repair_resources(["resource"], **options)
    assert (second.repaired, second.exit_code) == (1, 0)
    flow.client.structured_complete.assert_not_awaited()
    assert writer.replace_sources.await_count == 2
    assert len(writer.replace_sources.await_args.args[0]) == len(chunks)


async def test_graph_stage_after_facts_and_failed_resource_continues(repair, flow):
    await prepare(flow, "bad")
    await prepare(flow, "good")
    observed = []

    async def index(chunks):
        observed.append(chunks[0].resource_id)
        stored = await flow.content.get_chunks([chunks[0].chunk_id])
        assert (
                stored[0].metadata["graph_version"] == chunks[0].metadata["graph_version"]
        )
        if chunks[0].resource_id == "bad":
            raise OSError("graph failed")

    graph = SimpleNamespace(index=index)
    report = await repair.repair_resources(
        ["bad", "good"],
        publication=flow.publication,
        content=flow.content,
        extractor=flow.extractor,
        facts=flow.facts,
        graph=graph,
    )
    assert (report.repaired, report.document_errors, report.exit_code) == (1, 1, 1)
    assert observed == ["bad", "good"]


async def test_fact_failure_prevents_graph_write(repair, flow):
    await prepare(flow)
    flow.facts.index = AsyncMock(side_effect=OSError("facts failed"))
    graph = SimpleNamespace(index=AsyncMock())
    report = await repair.repair_resources(
        ["resource"],
        publication=flow.publication,
        content=flow.content,
        extractor=flow.extractor,
        facts=flow.facts,
        graph=graph,
    )
    assert report.document_errors == 1
    graph.index.assert_not_awaited()


def graph_arguments():
    return [
        "--with-graph",
        "--neo4j-uri",
        "neo4j://isolated",
        "--neo4j-username",
        "neo4j",
        "--neo4j-password",
        "test",
    ]


def test_cli_graph_opt_in_defaults_and_environment_validation(
        repair, tmp_path, graph_ontology, monkeypatch
):
    for name in ("URI", "USERNAME", "PASSWORD", "DATABASE", "GRAPH_NAME"):
        monkeypatch.delenv(f"RAG_GRAPH_REPAIR_NEO4J_{name}", raising=False)
    arguments = [
        *config_arguments(tmp_path, graph_ontology),
        "--resource-id",
        "resource",
    ]
    assert repair.parse_args(arguments).with_graph is False
    with pytest.raises(SystemExit):
        repair.parse_args([*arguments, "--with-graph"])
    args = repair.parse_args([*arguments, *graph_arguments()])
    assert args.neo4j_database == "neo4j" and args.neo4j_graph_name == "rag_v4"
    with pytest.raises(SystemExit):
        repair.parse_args(
            [*arguments, *graph_arguments(), "--neo4j-graph-name", "bad`name"]
        )
    monkeypatch.setenv("RAG_GRAPH_REPAIR_NEO4J_URI", "neo4j://isolated")
    monkeypatch.setenv("RAG_GRAPH_REPAIR_NEO4J_USERNAME", "user")
    monkeypatch.setenv("RAG_GRAPH_REPAIR_NEO4J_PASSWORD", "test")
    args = repair.parse_args([*arguments, "--with-graph"])
    assert args.neo4j_username == "user"


@pytest.mark.parametrize("failure", [None, "schema", "repair", "cancel"])
async def test_graph_mode_explicit_initialization_and_all_clients_close(
        repair, monkeypatch, tmp_path, graph_ontology, failure
):
    args = repair.parse_args(
        [
            *config_arguments(tmp_path, graph_ontology),
            "--resource-id",
            "resource",
            *graph_arguments(),
        ]
    )
    mongo = DatabaseClient()
    api = SimpleNamespace(close=AsyncMock())
    driver = SimpleNamespace(close=AsyncMock())
    topology = SimpleNamespace(ensure_schema=AsyncMock())
    if failure == "schema":
        topology.ensure_schema.side_effect = OSError("schema failed")
    execute = AsyncMock(return_value=repair.RepairReport(repaired=1))
    if failure == "repair":
        execute.side_effect = OSError("repair failed")
    elif failure == "cancel":
        execute.side_effect = asyncio.CancelledError()
    monkeypatch.setattr(repair, "AsyncMongoClient", Mock(return_value=mongo))
    monkeypatch.setattr(repair, "AsyncOpenAI", Mock(return_value=api))
    monkeypatch.setattr(
        repair, "InstructionClient", Mock(return_value=SimpleNamespace(model="model"))
    )
    factory = Mock(return_value=driver)
    monkeypatch.setattr(repair, "AsyncGraphDatabase", SimpleNamespace(driver=factory))
    monkeypatch.setattr(repair, "Neo4jGraphStorage", Mock(return_value=topology))
    monkeypatch.setattr(repair, "repair_resources", execute)
    if failure:
        with pytest.raises((OSError, asyncio.CancelledError)):
            await repair.run(args)
    else:
        assert (await repair.run(args)).repaired == 1
        assert isinstance(execute.await_args.kwargs["graph"], GraphIndexer)
    topology.ensure_schema.assert_awaited_once()
    for client in (mongo, api, driver):
        client.close.assert_awaited_once()


async def test_graph_disabled_does_not_create_driver(
        repair, monkeypatch, tmp_path, graph_ontology
):
    args = repair.parse_args(
        [*config_arguments(tmp_path, graph_ontology), "--resource-id", "resource"]
    )
    mongo = DatabaseClient()
    api = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(repair, "AsyncMongoClient", Mock(return_value=mongo))
    monkeypatch.setattr(repair, "AsyncOpenAI", Mock(return_value=api))
    monkeypatch.setattr(
        repair, "InstructionClient", Mock(return_value=SimpleNamespace(model="model"))
    )
    factory = Mock()
    monkeypatch.setattr(repair, "AsyncGraphDatabase", SimpleNamespace(driver=factory))
    execute = AsyncMock(return_value=repair.RepairReport(repaired=1))
    monkeypatch.setattr(repair, "repair_resources", execute)
    await repair.run(args)
    factory.assert_not_called()
    assert execute.await_args.kwargs["graph"] is None


def vector_arguments():
    return [
        "--with-graph-vectors",
        "--qdrant-url",
        "http://isolated",
        "--embedding-base-url",
        "http://embedding/v1",
        "--embedding-api-key",
        "test",
        "--embedding-model",
        "embedding",
        "--embedding-dimensions",
        "2",
    ]


async def test_projection_only_skips_extraction_and_fact_write(repair, flow):
    _, chunks = await prepare(flow)
    await run_repair(repair, flow, ["resource"])
    before = await flow.content.get_chunks([chunk.chunk_id for chunk in chunks])
    flow.client.structured_complete.reset_mock()
    facts = SimpleNamespace(index=AsyncMock())
    writer = SimpleNamespace(replace_sources=AsyncMock())
    report = await repair.repair_resources(
        ["resource"],
        publication=flow.publication,
        content=flow.content,
        extractor=flow.extractor,
        facts=facts,
        graph=GraphIndexer(ontology=flow.ontology, writer=writer),
        projection_only=True,
    )
    assert report.repaired == 1 and report.exit_code == 0
    flow.client.structured_complete.assert_not_awaited()
    facts.index.assert_not_awaited()
    writer.replace_sources.assert_awaited_once()
    assert await flow.content.get_chunks([chunk.chunk_id for chunk in chunks]) == before


async def test_projection_only_bad_fact_input_does_not_write(repair, flow):
    await prepare(flow)
    writer = SimpleNamespace(replace_sources=AsyncMock())
    report = await repair.repair_resources(
        ["resource"],
        publication=flow.publication,
        content=flow.content,
        graph=GraphIndexer(ontology=flow.ontology, writer=writer),
        projection_only=True,
    )
    assert report.document_errors == 1
    writer.replace_sources.assert_not_awaited()
    flow.client.structured_complete.assert_not_awaited()


async def test_projection_only_state_change_skips_indexer(repair, flow):
    await prepare(flow)
    original = flow.publication.get_states
    calls = 0

    async def get_states(ids):
        nonlocal calls
        calls += 1
        if calls == 2:
            await flow.publication.destroy_resources(ids)
        return await original(ids)

    flow.publication.get_states = get_states
    graph = SimpleNamespace(index=AsyncMock())
    report = await repair.repair_resources(
        ["resource"],
        publication=flow.publication,
        content=flow.content,
        graph=graph,
        projection_only=True,
    )
    assert report.skipped == 1
    graph.index.assert_not_awaited()


async def test_combined_indexer_failure_keeps_mongo_cache_and_continues(repair, flow):
    await prepare(flow, "bad")
    await prepare(flow, "good")
    seen = []

    async def index(chunks):
        assert (
                "graph_version"
                in (await flow.content.get_chunks([chunks[0].chunk_id]))[0].metadata
        )
        seen.append(chunks[0].resource_id)
        if seen[-1] == "bad":
            raise OSError("vector partial write")

    graph = SimpleNamespace(index=AsyncMock(side_effect=index))
    options = {
        "publication": flow.publication,
        "content": flow.content,
        "extractor": flow.extractor,
        "facts": flow.facts,
        "graph": graph,
    }
    report = await repair.repair_resources(["bad", "good"], **options)
    assert (report.repaired, report.document_errors) == (1, 1)
    flow.client.structured_complete.reset_mock()
    graph.index.side_effect = None
    assert (await repair.repair_resources(["bad"], **options)).repaired == 1
    flow.client.structured_complete.assert_not_awaited()
    assert seen == ["bad", "good"]


def test_cli_projection_only_vector_configuration(
        repair, tmp_path, graph_ontology, monkeypatch
):
    arguments = [
        *config_arguments(tmp_path, graph_ontology)[:6],
        "--resource-id",
        "resource",
    ]
    args = repair.parse_args([*arguments, "--projection-only", *vector_arguments()])
    assert args.instruction_model is None
    assert args.entity_vector_collection == "graph_entity_source_vectors_v4"
    assert args.relation_vector_collection == "graph_relation_source_vectors_v4"
    for extra in (
            ["--projection-only"],
            ["--projection-only", *vector_arguments(), "--force"],
            ["--projection-only", *vector_arguments(), "--gleaning"],
            ["--projection-only", *vector_arguments(), "--embedding-dimensions", "0"],
            [
                "--projection-only",
                *vector_arguments(),
                "--entity-vector-collection",
                "same",
                "--relation-vector-collection",
                "same",
            ],
    ):
        with pytest.raises(SystemExit):
            repair.parse_args([*arguments, *extra])
    for name, value in (
            ("QDRANT_URL", "http://isolated"),
            ("EMBEDDING_BASE_URL", "http://embedding/v1"),
            ("EMBEDDING_API_KEY", "test"),
            ("EMBEDDING_MODEL", "model"),
            ("EMBEDDING_DIMENSIONS", "2"),
    ):
        monkeypatch.setenv(f"RAG_GRAPH_REPAIR_{name}", value)
    args = repair.parse_args([*arguments, "--projection-only", "--with-graph-vectors"])
    assert args.embedding_dimensions == 2 and args.embedding_model == "model"


@pytest.mark.parametrize("failure", [None, "schema", "repair", "cancel"])
async def test_vectors_only_setup_no_instruction_or_neo4j_and_closes_clients(
        repair, monkeypatch, tmp_path, graph_ontology, failure
):
    args = repair.parse_args(
        [
            *config_arguments(tmp_path, graph_ontology)[:6],
            "--resource-id",
            "resource",
            "--projection-only",
            *vector_arguments(),
        ]
    )
    mongo = DatabaseClient()
    api = SimpleNamespace(close=AsyncMock())
    qdrant = SimpleNamespace(close=AsyncMock())
    vectors = [
        SimpleNamespace(ensure_collection=AsyncMock()),
        SimpleNamespace(ensure_collection=AsyncMock()),
    ]
    if failure == "schema":
        vectors[1].ensure_collection.side_effect = OSError("index failure")
    execute = AsyncMock(return_value=repair.RepairReport(repaired=1))
    if failure == "repair":
        execute.side_effect = OSError("write failed")
    elif failure == "cancel":
        execute.side_effect = asyncio.CancelledError()
    monkeypatch.setattr(repair, "AsyncMongoClient", Mock(return_value=mongo))
    monkeypatch.setattr(repair, "AsyncOpenAI", Mock(return_value=api))
    instruction = Mock()
    monkeypatch.setattr(repair, "InstructionClient", instruction)
    driver = Mock()
    monkeypatch.setattr(repair, "AsyncGraphDatabase", SimpleNamespace(driver=driver))
    monkeypatch.setattr(repair, "AsyncQdrantClient", Mock(return_value=qdrant))
    storage = Mock(side_effect=vectors)
    monkeypatch.setattr(repair, "QdrantVectorStorage", storage)
    monkeypatch.setattr(repair, "repair_resources", execute)
    if failure:
        with pytest.raises((OSError, asyncio.CancelledError)):
            await repair.run(args)
    else:
        assert (await repair.run(args)).repaired == 1
        assert (
                execute.await_args.kwargs["extractor"] is None
                and execute.await_args.kwargs["facts"] is None
        )
    for storage_instance in vectors:
        storage_instance.ensure_collection.assert_awaited_once_with(
            metadata_index_fields=[
                IndexableField(name, IndexKind.KEYWORD)
                for name in (
                    "resource_id",
                    "content_revision",
                    "chunk_id",
                    "ontology_name",
                )
            ]
        )
    assert all(
        call.kwargs["sparse_vector_name"] is None for call in storage.call_args_list
    )
    instruction.assert_not_called()
    driver.assert_not_called()
    for client in (mongo, api, qdrant):
        client.close.assert_awaited_once()

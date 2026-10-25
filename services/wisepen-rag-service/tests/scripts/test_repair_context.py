"""离线 repair 仅装配替身：维护窗口、增量生成和全部向量重写。"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from rag.application.publication import DocumentReadyEvent
from rag.core.schema import ContentRevision
from rag.ingestion.chunkers import MarkdownChunker
from rag.ingestion.indexers import ChunkFactsIndexer, VectorIndexer
from rag.ingestion.transformers import Contextualizer


@pytest.fixture(scope="module")
def repair():
    path = Path(__file__).resolve().parents[2] / "scripts" / "repair_context.py"
    spec = importlib.util.spec_from_file_location("_test_rag_repair_context", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def flow(storage, publication_store):
    client = SimpleNamespace(model="model", complete=AsyncMock(return_value="上下文"))
    embedding = SimpleNamespace(
        dimensions=2,
        embed=AsyncMock(
            side_effect=lambda **kwargs: [[1.0, 0.0] for _ in kwargs["texts"]]
        ),
    )
    writer = SimpleNamespace(upsert=AsyncMock())
    return SimpleNamespace(
        client=client,
        embedding=embedding,
        writer=writer,
        publication=publication_store,
        content=storage,
        contextualizer=Contextualizer(client=client),
        facts=ChunkFactsIndexer(storage),
        vectors=VectorIndexer(embedding_client=embedding, writer=writer),
    )


async def prepare(
        flow,
        resource_id="resource",
        *,
        version=1,
        applied=True,
        text="# 标题\n\n正文\n\n## 子标题\n\n内容",
):
    document = DocumentReadyEvent.model_validate(
        {"resourceId": resource_id, "version": version, "content": text}
    ).to_document()
    revision = ContentRevision.create(
        resource_id=resource_id, document_version=version, text=text
    )
    await flow.content.upsert_documents([document])
    chunks = await MarkdownChunker().split(document)
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
        contextualizer=flow.contextualizer,
        facts=flow.facts,
        vectors=flow.vectors,
    )


async def test_applied_only_explicit_scope_and_all_vector_rewrites(repair, flow):
    doc, chunks = await prepare(flow)
    await prepare(flow, "staged", applied=False)
    await prepare(flow, "destroyed")
    await flow.publication.destroy_resources(["destroyed"])
    await prepare(flow, "not-selected")
    first = await run_repair(
        repair, flow, ["missing", "staged", "destroyed", "resource", "resource"]
    )
    assert (
               first.repaired,
               first.skipped,
               first.generated,
               first.reused,
               first.exit_code,
           ) == (1, 3, len(chunks), 0, 0)
    texts = flow.embedding.embed.await_args.kwargs["texts"]
    assert all("上下文" in text and "标题" in text for text in texts)
    persisted = await flow.content.get_revision_chunks(
        resource_id=doc.resource_id, content_revision=doc.content_revision
    )
    assert [chunk.raw_text for chunk in persisted] == [
        chunk.raw_text for chunk in chunks
    ]
    assert all(chunk.metadata["context_status"] == "ok" for chunk in persisted)
    flow.client.complete.reset_mock()
    flow.writer.upsert.reset_mock()
    second = await run_repair(repair, flow, ["resource"])
    assert (second.generated, second.reused, second.repaired, second.exit_code) == (
        0,
        len(chunks),
        1,
        0,
    )
    flow.client.complete.assert_not_awaited()
    assert len(flow.writer.upsert.await_args.args[0]) == len(chunks)
    state = (await flow.publication.get_states(["resource"]))[0]
    assert state.applied_revision.content_revision == doc.content_revision


@pytest.mark.parametrize("change", ["new-applied", "staged", "destroyed"])
async def test_state_change_after_generation_skips_all_writes(repair, flow, change):
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
        return "上下文"

    flow.client.complete.side_effect = generate
    flow.facts.index = AsyncMock()
    report = await run_repair(repair, flow, ["resource"])
    assert report.skipped == 1 and report.repaired == 0
    flow.facts.index.assert_not_awaited()
    flow.writer.upsert.assert_not_awaited()


@pytest.mark.parametrize("failure", ["facts", "vectors"])
async def test_partial_write_failure_then_rerun_repairs_all_vectors(
        repair, flow, failure
):
    _, chunks = await prepare(flow)
    if failure == "vectors":
        flow.writer.upsert.side_effect = OSError("vector write failure")
    else:
        original = flow.facts.index
        flow.facts.index = AsyncMock(side_effect=OSError("facts write failure"))
    first = await run_repair(repair, flow, ["resource"])
    assert first.document_errors == 1 and first.exit_code == 1
    if failure == "facts":
        flow.facts.index = original
    else:
        flow.writer.upsert.side_effect = None
    calls = flow.client.complete.await_count
    flow.writer.upsert.reset_mock()
    second = await run_repair(repair, flow, ["resource"])
    assert second.repaired == 1 and second.exit_code == 0
    assert len(flow.writer.upsert.await_args.args[0]) == len(chunks)
    if failure == "vectors":
        assert second.reused == len(chunks)
        assert flow.client.complete.await_count == calls
    else:
        assert second.generated == len(chunks)


async def test_model_failed_results_are_persisted_indexed_and_report_nonzero(
        repair, flow
):
    doc, chunks = await prepare(flow)
    flow.client.complete.side_effect = TimeoutError("model timeout")
    report = await run_repair(repair, flow, ["resource"])
    assert (
            report.failed == len(chunks) and report.exit_code == 1 and report.repaired == 1
    )
    texts = flow.embedding.embed.await_args.kwargs["texts"]
    assert texts == [chunk.retrieval_text for chunk in chunks]
    persisted = await flow.content.get_revision_chunks(
        resource_id=doc.resource_id, content_revision=doc.content_revision
    )
    assert all(chunk.metadata == {"context_status": "failed"} for chunk in persisted)


async def test_budget_fallback_is_indexed_and_reported_without_model(repair, flow):
    _, chunks = await prepare(flow)
    flow.contextualizer = Contextualizer(client=flow.client, max_context_tokens=1)
    report = await run_repair(repair, flow, ["resource"])
    assert report.fallback == len(chunks) and report.repaired == 1
    assert report.exit_code == 0
    flow.client.complete.assert_not_awaited()
    assert len(flow.writer.upsert.await_args.args[0]) == len(chunks)


@pytest.mark.parametrize("text", ["", " \n\n"])
async def test_valid_zero_chunk_document_does_not_invent_missing_facts(
        repair, flow, text
):
    _, chunks = await prepare(flow, text=text)
    assert chunks == []
    report = await run_repair(repair, flow, ["resource"])
    assert report.repaired == 1 and report.exit_code == 0
    flow.client.complete.assert_not_awaited()
    flow.writer.upsert.assert_not_awaited()


async def test_bad_document_does_not_prevent_next_resource(repair, flow):
    bad, _ = await prepare(flow, "bad")
    await prepare(flow, "good")
    bad.text = "身份被破坏"
    await flow.content.upsert_documents([bad])
    report = await run_repair(repair, flow, ["bad", "good"])
    assert (report.document_errors, report.repaired, report.exit_code) == (1, 1, 1)


async def test_backend_query_failure_is_document_scoped(repair, flow):
    original = flow.publication.get_states
    await prepare(flow, "good")

    async def states(ids):
        if ids == ["bad"]:
            raise OSError("database unavailable")
        return await original(ids)

    flow.publication.get_states = states
    report = await run_repair(repair, flow, ["bad", "good"])
    assert (report.document_errors, report.repaired, report.exit_code) == (1, 1, 1)


def config_arguments():
    return [
        "--mongo-url",
        "mongodb://isolated",
        "--mongo-database",
        "isolated",
        "--qdrant-url",
        "http://isolated",
        "--instruction-base-url",
        "http://isolated/v1",
        "--instruction-api-key",
        "test",
        "--instruction-model",
        "model",
        "--embedding-base-url",
        "http://isolated/v1",
        "--embedding-api-key",
        "test",
        "--embedding-model",
        "embedding",
        "--embedding-dimensions",
        "2",
    ]


def test_cli_explicit_resources_deduplicate_and_validate_before_io(repair, tmp_path):
    path = tmp_path / "resources.txt"
    path.write_text("b\na\n\n中文资源\n", encoding="utf-8")
    args = repair.parse_args(
        [
            *config_arguments(),
            "--resource-id",
            "a",
            "--resource-id",
            "a",
            "--resource-file",
            str(path),
        ]
    )
    assert args.resource_ids == ["a", "b", "中文资源"]
    assert args.vector_collection == "document_chunk_vectors_v4"
    for extra in (
            [],
            ["--resource-id", "a", "--max-context-tokens", "0"],
            ["--resource-file", str(tmp_path / "missing")],
    ):
        with pytest.raises(SystemExit):
            repair.parse_args([*config_arguments(), *extra])


@pytest.mark.parametrize("fail_initialization", [False, True])
async def test_run_closes_every_client_on_success_and_initialization_failure(
        repair, monkeypatch, fail_initialization
):
    args = repair.parse_args([*config_arguments(), "--resource-id", "resource"])
    mongo = MagicDatabaseClient()
    qdrant = SimpleNamespace(close=AsyncMock())
    instruction_api = SimpleNamespace(close=AsyncMock())
    embedding_api = SimpleNamespace(close=AsyncMock())
    apis = Mock(side_effect=[instruction_api, embedding_api])
    vector_storage = SimpleNamespace(ensure_collection=AsyncMock())
    if fail_initialization:
        vector_storage.ensure_collection.side_effect = OSError(
            "cannot initialize collection"
        )
    monkeypatch.setattr(repair, "AsyncMongoClient", Mock(return_value=mongo))
    monkeypatch.setattr(repair, "AsyncQdrantClient", Mock(return_value=qdrant))
    monkeypatch.setattr(repair, "AsyncOpenAI", apis)
    monkeypatch.setattr(
        repair, "InstructionClient", Mock(return_value=SimpleNamespace(model="model"))
    )
    monkeypatch.setattr(
        repair, "EmbeddingClient", Mock(return_value=SimpleNamespace(dimensions=2))
    )
    monkeypatch.setattr(
        repair, "QdrantVectorStorage", Mock(return_value=vector_storage)
    )
    monkeypatch.setattr(
        repair,
        "repair_resources",
        AsyncMock(return_value=repair.RepairReport(repaired=1)),
    )
    if fail_initialization:
        with pytest.raises(OSError):
            await repair.run(args)
    else:
        assert (await repair.run(args)).repaired == 1
    for client in (mongo, qdrant, instruction_api, embedding_api):
        client.close.assert_awaited_once()


class MagicDatabaseClient:
    def __init__(self):
        self.close = AsyncMock()

    def __getitem__(self, name):
        return self


def test_main_nonzero_and_report_without_running_network(repair, monkeypatch, capsys):
    monkeypatch.setattr(
        repair, "run", AsyncMock(return_value=repair.RepairReport(failed=2))
    )
    assert repair.main([*config_arguments(), "--resource-id", "resource"]) == 1
    assert '"failed": 2' in capsys.readouterr().out

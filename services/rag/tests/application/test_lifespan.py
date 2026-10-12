"""真实入口的就绪与清理时序；只替换外部边界，不连接业务后端。"""

import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI

from rag.container import Container


@pytest.fixture
def runtime(monkeypatch, platform_settings):
    bootstrap = SimpleNamespace(
        LOG_LEVEL="INFO",
        SERVICE_NAME="rag-test",
        PROFILE="test",
        APP_NAME="RAG test",
        IS_DEV=True,
    )
    nacos = SimpleNamespace(
        register_instance=AsyncMock(), deregister_instance=AsyncMock()
    )
    for name, field, value in [
        ("bootstrap_settings", "bootstrap_settings", bootstrap),
        ("nacos", "nacos_client_manager", nacos),
    ]:
        module = ModuleType(f"rag.core.config.{name}")
        setattr(module, field, value)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr("rag.core.config.app_settings.settings", platform_settings)
    for name in (
        "common.logger.setup_logging_intercept",
        "common.observability.setup_observability",
        "common.observability.instrument_fastapi_app",
        "common.web.exception_handlers.setup_global_exception_handlers",
    ):
        monkeypatch.setattr(name, Mock())
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.delitem(sys.modules, "rag.main", raising=False)
    main = importlib.import_module("rag.main")
    container = Container(settings=platform_settings)
    mongo = SimpleNamespace(
        close=AsyncMock(), admin=SimpleNamespace(command=AsyncMock())
    )
    clients = [mongo] + [SimpleNamespace(close=AsyncMock()) for _ in range(4)]
    clients[-1].verify_connectivity = AsyncMock()
    for name, client in zip(
        (
            "mongo_client",
            "qdrant_client",
            "embedding_openai",
            "instruction_openai",
            "neo4j_driver",
        ),
        clients,
        strict=True,
    ):
        getattr(container, name).override(providers.Object(client))
    stores = {}
    for name in (
        "content_storage",
        "publication_store",
        "acl_repository",
        "vector_storage",
        "entity_vector_storage",
        "relationship_vector_storage",
        "graph_storage",
    ):
        storage = SimpleNamespace(
            ensure_indexes=AsyncMock(),
            ensure_collection=AsyncMock(),
            ensure_schema=AsyncMock(),
        )
        stores[name] = storage
        getattr(container, name).override(
            providers.Object(None if name == "graph_storage" else storage)
        )
    for name in ("embedding_client", "rerank_client", "pipeline"):
        getattr(container, name).override(providers.Object(object()))
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def gc_run():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    gc = SimpleNamespace(run=AsyncMock(side_effect=gc_run))
    container.garbage_collector.override(providers.Object(gc))
    consumers = [SimpleNamespace(run=AsyncMock()) for _ in range(2)]
    for name, consumer in zip(
        ("acl_consumer", "publication_consumer"), consumers, strict=True
    ):
        getattr(container, name).override(providers.Object(consumer))
    app = FastAPI(lifespan=main.lifespan)
    app.state.container = container
    app.state.ready = False
    app.state.service_name = "rag-test"
    app.include_router(main.api_router, prefix="/rag")
    yield SimpleNamespace(
        main=main,
        app=app,
        container=container,
        clients=clients,
        stores=stores,
        gc=gc,
        gc_started=started,
        gc_cancelled=cancelled,
        consumers=consumers,
        nacos=nacos,
    )
    sys.modules.pop("rag.main", None)


async def health(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/rag/health")


def enable_kafka(env):
    values = env.container.settings().model_dump() | {
        "KAFKA_ENABLED": True,
        "KAFKA_BOOTSTRAP_SERVERS": "localhost:9092",
    }
    env.container.settings.override(
        providers.Object(type(env.container.settings())(**values))
    )


async def test_kafka_off_gc_always_starts_and_ready_does_not_wait_for_first_round(
    runtime,
):
    env = runtime
    async with env.main.lifespan(env.app):
        assert (await health(env.app)).status_code == 200
        await asyncio.wait_for(env.gc_started.wait(), 1)
        for consumer in env.consumers:
            consumer.run.assert_not_called()
        assert not env.gc_cancelled.is_set()
    assert (await health(env.app)).status_code == 503 and env.gc_cancelled.is_set()
    for client in env.clients[:3]:
        client.close.assert_awaited_once()
    for client in env.clients[3:]:
        client.close.assert_not_awaited()
    for name in ("content_storage", "publication_store", "acl_repository"):
        env.stores[name].ensure_indexes.assert_awaited_once()
    env.stores["vector_storage"].ensure_collection.assert_awaited_once_with()
    for name in ("entity_vector_storage", "relationship_vector_storage"):
        env.stores[name].ensure_collection.assert_awaited_once_with(
            metadata_index_fields=[
                "resource_id",
                "content_revision",
                "chunk_id",
                "ontology_name",
            ]
        )


async def test_handshake_waits_for_both_consumers_before_ready(runtime):
    env = runtime
    enable_kafka(env)
    allow = [asyncio.Event(), asyncio.Event()]
    entered = [asyncio.Event(), asyncio.Event()]
    for index, consumer in enumerate(env.consumers):

        async def consume(*, started, index=index):
            entered[index].set()
            await allow[index].wait()
            started.set()
            await asyncio.Event().wait()

        consumer.run.side_effect = consume
    ready, release = asyncio.Event(), asyncio.Event()

    async def serve():
        async with env.main.lifespan(env.app):
            ready.set()
            await release.wait()

    task = asyncio.create_task(serve())
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), 1)
        assert (await health(env.app)).status_code == 503
        allow[0].set()
        await asyncio.sleep(0)
        assert not ready.is_set()
        allow[1].set()
        await asyncio.wait_for(ready.wait(), 1)
        assert (await health(env.app)).status_code == 200
    finally:
        release.set()
        await asyncio.wait_for(task, 1)


@pytest.mark.parametrize("when", ["startup", "running", "normal_return"])
async def test_consumer_exit_revokes_ready_and_cancels_other_tasks(runtime, when):
    env = runtime
    enable_kafka(env)
    fail = asyncio.Event()

    async def failing(*, started):
        if when != "startup":
            started.set()
            await fail.wait()
        if when != "normal_return":
            raise OSError("consumer exited")

    async def healthy(*, started):
        started.set()
        await asyncio.Event().wait()

    env.consumers[0].run.side_effect = failing
    env.consumers[1].run.side_effect = healthy
    with pytest.raises(ExceptionGroup) as caught:
        async with env.main.lifespan(env.app):
            fail.set()
            await asyncio.Event().wait()
    assert isinstance(
        caught.value.exceptions[0], RuntimeError if when == "normal_return" else OSError
    )
    assert (await health(env.app)).status_code == 503 and env.gc_cancelled.is_set()
    for client in env.clients[:3]:
        client.close.assert_awaited_once()


@pytest.mark.parametrize(
    "stage",
    [
        "mongo",
        "content_storage",
        "publication_store",
        "acl_repository",
        "vector_storage",
        "entity_vector_storage",
        "relationship_vector_storage",
        "pipeline",
    ],
)
async def test_initialization_failure_cleans_created_clients_and_never_ready(
    runtime, stage
):
    env = runtime
    if stage == "mongo":
        env.clients[0].admin.command.side_effect = OSError("init")
    elif stage == "pipeline":
        env.container.pipeline.override(
            providers.Callable(Mock(side_effect=OSError("init")))
        )
    else:
        method = (
            "ensure_indexes"
            if stage in ("content_storage", "publication_store", "acl_repository")
            else "ensure_collection"
        )
        getattr(env.stores[stage], method).side_effect = OSError("init")
    with pytest.raises(OSError):
        async with env.main.lifespan(env.app):
            pytest.fail("must not yield")
    assert not env.app.state.ready
    env.gc.run.assert_not_called()
    for client in env.clients[:3]:
        client.close.assert_awaited_once()


async def test_optional_clients_shared_override_close_once(runtime):
    env = runtime
    values = env.container.settings().model_dump() | {
        "ENABLE_GRAPH_STORAGE": True,
        "NEO4J_URI": "neo4j://localhost:7687",
        "NEO4J_USERNAME": "test",
        "NEO4J_PASSWORD": "test",
        "INSTRUCTION_BASE_URL": "http://localhost:8000",
        "INSTRUCTION_API_KEY": "test",
        "INSTRUCTION_MODEL": "test",
    }
    env.container.settings.override(
        providers.Object(type(env.container.settings())(**values))
    )
    env.container.graph_storage.override(providers.Object(env.stores["graph_storage"]))
    env.container.instruction_openai.override(providers.Object(env.clients[2]))
    async with env.main.lifespan(env.app):
        env.clients[-1].verify_connectivity.assert_awaited_once()
        env.stores["graph_storage"].ensure_schema.assert_awaited_once()
    env.clients[2].close.assert_awaited_once()
    env.clients[-1].close.assert_awaited_once()


async def test_partial_client_construction_failure_closes_previous_clients(runtime):
    env = runtime
    env.container.embedding_openai.override(
        providers.Callable(Mock(side_effect=OSError("construct")))
    )
    with pytest.raises(OSError):
        async with env.main.lifespan(env.app):
            pytest.fail("construction failed")
    for client in env.clients[:2]:
        client.close.assert_awaited_once()


async def test_cancel_waits_for_tasks_then_closes_clients(runtime):
    env = runtime

    async def close():
        assert env.gc_cancelled.is_set()

    env.clients[0].close.side_effect = close

    async def serve():
        async with env.main.lifespan(env.app):
            await asyncio.Event().wait()

    task = asyncio.create_task(serve())
    await asyncio.wait_for(env.gc_started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not env.app.state.ready
    env.clients[0].close.assert_awaited_once()


async def test_gc_unexpected_exit_is_observed(runtime):
    env = runtime
    env.gc.run.side_effect = None
    with pytest.raises(ExceptionGroup, match="TaskGroup"):
        async with env.main.lifespan(env.app):
            await asyncio.Event().wait()
    assert not env.app.state.ready


async def test_gc_scan_errors_do_not_revoke_ready(runtime):
    from rag.application.gc import ResourceGarbageCollector

    env = runtime
    store = SimpleNamespace(
        list_destroyed_resource_ids=AsyncMock(side_effect=OSError("scan"))
    )
    collector = ResourceGarbageCollector(
        store=store,
        content_writer=object(),
        resource_acls=object(),
        vector_writers=[],
        graph_writers=[],
        interval_seconds=60,
    )
    scanned = asyncio.Event()

    async def scan(**kwargs):
        scanned.set()
        raise OSError("scan")

    store.list_destroyed_resource_ids.side_effect = scan
    env.container.garbage_collector.override(providers.Object(collector))
    async with env.main.lifespan(env.app):
        await asyncio.wait_for(scanned.wait(), 1)
        assert (await health(env.app)).status_code == 200


@pytest.mark.parametrize("stage", ["connection", "construction", "schema"])
async def test_neo4j_initialization_failure_closes_owned_driver(runtime, stage):
    env = runtime
    values = env.container.settings().model_dump() | {
        "ENABLE_GRAPH_STORAGE": True,
        "NEO4J_URI": "neo4j://localhost:7687",
        "NEO4J_USERNAME": "test",
        "NEO4J_PASSWORD": "test",
    }
    env.container.settings.override(
        providers.Object(type(env.container.settings())(**values))
    )
    env.container.graph_storage.override(providers.Object(env.stores["graph_storage"]))
    if stage == "connection":
        env.clients[-1].verify_connectivity.side_effect = OSError("graph")
    elif stage == "construction":
        env.container.graph_storage.override(
            providers.Callable(Mock(side_effect=OSError("graph")))
        )
    else:
        env.stores["graph_storage"].ensure_schema.side_effect = OSError("graph")
    with pytest.raises(OSError):
        async with env.main.lifespan(env.app):
            pytest.fail("initialization failed")
    env.clients[-1].close.assert_awaited_once()
    assert not env.app.state.ready


async def test_nacos_register_failure_keeps_initialized_service_ready(runtime):
    env = runtime
    env.nacos.register_instance.side_effect = OSError("register")
    async with env.main.lifespan(env.app):
        assert env.app.state.ready
    env.nacos.deregister_instance.assert_awaited_once()

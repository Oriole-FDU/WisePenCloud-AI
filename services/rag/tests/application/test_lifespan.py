"""服务生命周期等待 Kafka 启动，并在消费基础设施失败后撤销健康可见性。"""

import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from dependency_injector import providers


@pytest.fixture
def runtime(monkeypatch):
    # 替代配置拉取与注册边界，保留真实 Container、FastAPI 和应用生命周期实现。
    package = ModuleType("rag.core.config")
    package.__path__ = []
    bootstrap = SimpleNamespace(
        LOG_LEVEL="INFO",
        SERVICE_NAME="rag-test",
        PROFILE="test",
        APP_NAME="RAG test",
        IS_DEV=True,
    )
    settings = SimpleNamespace(
        MONGODB_URL="mongodb://localhost:27017",
        MONGODB_DB_NAME="rag-test",
        resource_mongodb_db_name="resource-test",
        KAFKA_ENABLED=True,
        KAFKA_BOOTSTRAP_SERVERS="localhost:9092",
        KAFKA_RESOURCE_ACL_RECALC_TOPIC="acl",
        KAFKA_RAG_ACL_RECALC_GROUP_ID="rag-acl",
        KAFKA_MAX_POLL_INTERVAL_MS=300_000,
    )
    nacos = SimpleNamespace(
        register_instance=AsyncMock(), deregister_instance=AsyncMock()
    )
    monkeypatch.setitem(sys.modules, "rag.core.config", package)
    for name, field, value in [
        ("app_settings", "settings", settings),
        ("bootstrap_settings", "bootstrap_settings", bootstrap),
        ("nacos", "nacos_client_manager", nacos),
    ]:
        module = ModuleType(f"rag.core.config.{name}")
        setattr(module, field, value)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr("common.logger.setup_logging_intercept", Mock())
    monkeypatch.setattr("common.observability.setup_observability", Mock())
    monkeypatch.setattr("common.observability.instrument_fastapi_app", Mock())
    monkeypatch.setattr(
        "common.web.exception_handlers.setup_global_exception_handlers", Mock()
    )
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    names = ["rag.main", "rag.container", "rag.api.router"]
    for name in names:
        monkeypatch.delitem(sys.modules, name, raising=False)
    main = importlib.import_module("rag.main")
    mongo = SimpleNamespace(close=AsyncMock())
    consumer = SimpleNamespace(run=AsyncMock())
    with (
        main.container.mongo_client.override(providers.Object(mongo)),
        main.container.acl_consumer.override(providers.Object(consumer)),
    ):
        try:
            yield main, consumer, mongo, nacos
        finally:
            for name in names:
                sys.modules.pop(name, None)


async def health(main):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=main.app), base_url="http://test"
    ) as client:
        return await client.get("/rag/health")


async def test_waits_for_connection_then_cancels_and_cleans_up(runtime):
    main, consumer, mongo, nacos = runtime
    entering, allow_connection, ready, release = [asyncio.Event() for _ in range(4)]

    async def run(*, started):
        entering.set()
        await allow_connection.wait()
        started.set()
        await asyncio.Event().wait()

    consumer.run.side_effect = run

    async def serve():
        async with main.lifespan(main.app):
            ready.set()
            await release.wait()

    task = asyncio.create_task(serve())
    try:
        await asyncio.wait_for(entering.wait(), timeout=2)
        assert (await health(main)).status_code == 503
        allow_connection.set()
        await asyncio.wait_for(ready.wait(), timeout=2)
        response = await health(main)
        assert response.status_code == 200 and response.json()["status"] == "ok"
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=2)
    assert (await health(main)).status_code == 503
    mongo.close.assert_awaited_once()
    nacos.deregister_instance.assert_awaited_once()


@pytest.mark.parametrize("when", ["startup", "running"])
async def test_consumer_failure_is_observed_and_health_becomes_unavailable(
    runtime, when
):
    main, consumer, mongo, nacos = runtime
    error = OSError("Kafka unavailable")
    fail = asyncio.Event()

    async def run(*, started):
        if when == "running":
            started.set()
            await fail.wait()
        raise error

    consumer.run.side_effect = run
    with pytest.raises(ExceptionGroup) as caught:
        async with main.lifespan(main.app):
            assert when == "running"
            fail.set()
            await asyncio.Event().wait()
    assert caught.value.exceptions == (error,)
    assert (await health(main)).status_code == 503
    mongo.close.assert_awaited_once()
    nacos.deregister_instance.assert_awaited_once()

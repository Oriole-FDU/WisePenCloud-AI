"""完整检索链路预热就绪才向 Nacos 注册，异常关闭所有连接。"""

import asyncio
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI
from openai import AsyncOpenAI
from scholar import main
from scholar.api.endpoints.papers import router
from scholar.api.endpoints import embeddings
from common.web.middleware import SecurityHeaderMiddleware
from scholar.container import Container
from scholar.core.config.policy_settings import ScholarPolicyStore
from policy_fakes import make_policy_store
from scholar.domain.errors import ProviderError
from scholar.domain.models.paper import Paper
from scholar.domain.repositories.local_paper_repository import PaperCandidate


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "empty", "dependency_failure", "timeout", "encoder_failure"])
async def test_startup_requires_complete_arxiv_search_and_closes_resources(monkeypatch, outcome):
    events = []
    runtime_requests = []
    found = Paper("2501.00001", None, "A paper", "Abstract", [], date(2025, 1, 1), 2025, "https://arxiv.org/abs/2501.00001", None, "arxiv", {})

    class Encoder:
        def __init__(self, *args, **kwargs):
            pass

        def encode(self, inputs):
            events.append("encode")
            if outcome == "encoder_failure":
                raise RuntimeError("encoder unavailable")
            return [[1.0] * 768 for _ in inputs]

    class Papers:
        async def search(self, **kwargs):
            events.append("search")
            if outcome == "dependency_failure":
                raise httpx.ConnectError("Qdrant unavailable", request=httpx.Request("POST", "http://qdrant/points/query"))
            if outcome == "timeout":
                await asyncio.Event().wait()
            return [] if outcome == "empty" else [PaperCandidate(found, 1.0)]

    def runtime_embedding(request):
        runtime_requests.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0] * 768}]})

    api = AsyncOpenAI(
        api_key="local", base_url="http://encoder-not-listening/v1", max_retries=0,
        default_headers={"X-From-Source": "startup-test"},
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(runtime_embedding)),
    )
    clients = [httpx.AsyncClient() for _ in range(4)]
    qdrant, redis = AsyncMock(), AsyncMock()
    transport = AsyncMock()
    nacos = SimpleNamespace(register_instance=AsyncMock(), deregister_instance=AsyncMock())

    @asynccontextmanager
    async def watch(*args, **kwargs):
        yield

    local = Container()
    local.config.from_dict({"SCHOLAR_SEARCH_CANDIDATES": 200, "SCHOLAR_SEARCH_NUM_CANDIDATES": 1000, "SCHOLAR_SEARCH_RRF_K": 60, "SCHOLAR_SEARCH_CITATION_WEIGHT": 0.2, "SCHOLAR_SEARCH_INFLUENTIAL_WEIGHT": 0.1})
    local.policy_store.override(providers.Object(make_policy_store()))
    for name, value in {
        "paper_repository": Papers(), "embedding_api": api,
        "qdrant": qdrant, "redis": redis, "s2_transport": transport,
        **dict(zip(("openalex_http", "huggingface_http", "s2_http", "unpaywall_http"), clients, strict=True)),
    }.items():
        getattr(local, name).override(providers.Object(value))
    monkeypatch.setattr(main, "container", local)
    monkeypatch.setattr(main, "SpecterEncoder", Encoder)
    monkeypatch.setattr(main, "settings", SimpleNamespace(SCHOLAR_EMBEDDING_MODELS_PATH=None, SCHOLAR_EMBEDDING_CPU_THREADS=2, SCHOLAR_REQUEST_TIMEOUT_SECONDS=0.05 if outcome == "timeout" else 5, SCHOLAR_SEARCH_CANDIDATES=200))
    monkeypatch.setattr(main.bootstrap_settings, "NACOS_ENABLED", True)
    monkeypatch.setattr(main, "nacos_client_manager", nacos)
    monkeypatch.setattr(main, "watch_policies", watch)
    app = FastAPI()
    app.add_middleware(SecurityHeaderMiddleware, from_source_secret="startup-test")
    app.include_router(router)
    app.include_router(embeddings.router)

    if outcome in ("success", "empty"):
        async with main.lifespan(app):
            assert events == ["encode", "search"]
            assert runtime_requests == []  # 预热不能依赖尚未监听的 TCP 端口。
            nacos.register_instance.assert_awaited_once()
            # 预热使用的 ASGI 客户端关闭后，线上检索仍能使用自己的编码连接。
            result = await local.arxiv_searcher().search(query="runtime", field=main.AcademicField.COMPUTER_SCIENCE, published_after=None, published_before=None, limit=10)
            assert result.partial_error is None
            assert len(runtime_requests) == 1
        nacos.deregister_instance.assert_awaited_once()
        transport.aclose.assert_awaited_once()
    else:
        with pytest.raises(TimeoutError if outcome == "timeout" else ProviderError):
            async with main.lifespan(app):
                pytest.fail("failed startup must not serve requests")
        nacos.register_instance.assert_not_awaited()
        nacos.deregister_instance.assert_not_awaited()
        transport.start.assert_not_awaited()

    assert all(client.is_closed for client in clients)
    assert api.is_closed()
    qdrant.close.assert_awaited_once()
    redis.aclose.assert_awaited_once()

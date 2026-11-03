"""真实用例和 Redis 适配器经过 HTTP 出口时，缓存写入故障不能撤销读取结果。"""

from unittest.mock import AsyncMock

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI

from pdf_fakes import make_pdf_service
from policy_fakes import make_policy_store
from title_fakes import MemoryRedis
from scholar.api.endpoints import papers
from scholar.application.discovery import PaperDiscoveryService
from scholar.application.lookup import PaperLookupService
from scholar.application.lookup_runner import LookupBinding, LookupRunner
from scholar.application.search import PaperSearchService, SearchBinding
from scholar.container import container
from scholar.core.providers import PROVIDER_POLICIES
from scholar.core.storage.redis.discovery_cache import RedisDiscoveryCacheRepository
from scholar.core.storage.redis.paper_cache import RedisPaperCache
from scholar.core.storage.redis.query_cache import RedisQueryCacheRepository
from scholar.domain.interfaces.paper_discoverer import PaperDiscovery
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.interfaces.paper_searcher import PaperSearchBatch
from scholar.domain.models.fields import AcademicField, FieldPolicy
from scholar.domain.models.paper import Paper
from scholar.main import setup_exception_handlers


@pytest.fixture
async def scholar_http():
    redis = MemoryRedis()
    cache = RedisPaperCache(redis, "http-cache-write")
    found = Paper("1706.03762", None, "Retrieved paper", "Abstract", ["Author"], None, 2017,
                  "https://example.test/paper", "https://example.test/paper.pdf", "arxiv")
    provider = AsyncMock(
        search=AsyncMock(return_value=PaperSearchBatch([found])),
        lookup=AsyncMock(return_value=[PaperLookupMatch([found])]),
        discover=AsyncMock(return_value=PaperDiscovery([found], None, None)),
    )
    policies = make_policy_store({AcademicField.GENERAL: FieldPolicy(["arxiv"], discoverer="huggingface")}, common_enrichers=["arxiv"])
    runner = LookupRunner({"arxiv": LookupBinding(provider, cache, PROVIDER_POLICIES["arxiv"])})
    pdf = make_pdf_service()
    search = PaperSearchService({"arxiv": SearchBinding(provider)}, policies,
                                RedisQueryCacheRepository(redis, "http-cache-write", 60), runner, pdf=pdf)
    lookup = PaperLookupService(runner, pdf=pdf, policies=policies)
    discovery = PaperDiscoveryService({"huggingface": provider}, policies,
                                      RedisDiscoveryCacheRepository(redis, "http-cache-write"), pdf)
    app = FastAPI()
    app.include_router(papers.router)
    setup_exception_handlers(app)
    with (
        container.paper_search_service.override(providers.Object(search)),
        container.paper_lookup_service.override(providers.Object(lookup)),
        container.paper_discovery_service.override(providers.Object(discovery)),
        container.config.SCHOLAR_REQUEST_TIMEOUT_SECONDS.override(0.05),
    ):
        container.wire(modules=[papers])
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                yield client, redis, provider
        finally:
            container.unwire()


@pytest.mark.asyncio
@pytest.mark.parametrize(("operation", "payload"), [
    ("search", {"query": "retrieved paper"}),
    ("lookup", {"identifiers": ["1706.03762"]}),
    ("discover", {"field": "general"}),
])
async def test_http_returns_success_after_cache_write_failure(scholar_http, operation, payload, failed_cache_write):
    client, redis, _ = scholar_http
    redis.set = failed_cache_write
    response = await client.post(f"/papers/{operation}", json=payload, headers={"X-HuggingFace-Api-Key": "key"})

    assert response.status_code == 200
    data = response.json()["data"]
    paper = data["items"][0]["paper"] if operation == "lookup" else data["papers"][0]
    assert paper["title"] == "Retrieved paper"
    assert set(data) == ({"items"} if operation == "lookup" else {"papers", "limitations", "identity_conflicts"} if operation == "search" else {"papers", "window_start", "window_end"})
    if operation == "lookup":
        assert data["items"][0]["limitations"] == []
        assert "error" not in data["items"][0]
    elif operation == "search":
        assert data["limitations"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("operation", "payload"), [
    ("lookup", {"identifiers": ["1706.03762"]}),
    ("discover", {"field": "general"}),
])
async def test_http_read_deadline_remains_504(scholar_http, operation, payload):
    import asyncio

    client, _, provider = scholar_http

    async def blocked(*args, **kwargs):
        await asyncio.Event().wait()

    getattr(provider, operation).side_effect = blocked
    response = await client.post(f"/papers/{operation}", json=payload, headers={"X-HuggingFace-Api-Key": "key"})

    assert response.status_code == 504



@pytest.mark.asyncio
@pytest.mark.parametrize(("failure", "expected_status", "message"), [
    ("rate_limit", 429, "rate limited"),
    ("busy", 429, "rate limited"),
    ("timeout", 504, "timed out"),
    ("qdrant_timeout", 504, "timed out"),
    ("qdrant_gateway", 502, "unavailable"),
    ("credentials", 401, "credentials"),
    ("missing_credentials", 503, "API key"),
])
async def test_http_dependency_failures_keep_actionable_status_and_audit_details(scholar_http, monkeypatch, failure, expected_status, message):
    from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
    from scholar.domain.errors import ProviderBusy, ProviderCredentialsRequired, ProviderError

    audited = []
    monkeypatch.setattr("scholar.main.warning", lambda event, **fields: audited.append(fields))
    client, _, provider = scholar_http
    diagnostic = "private diagnostic at internal-stage https://upstream.test/detail"
    request = httpx.Request("GET", "https://upstream.test/detail")
    if failure == "busy":
        error = ProviderBusy(4, 3)
    elif failure == "missing_credentials":
        error = ProviderCredentialsRequired(diagnostic)
    else:
        cause = {
            "rate_limit": httpx.HTTPStatusError(diagnostic, request=request, response=httpx.Response(429, request=request)),
            "timeout": httpx.ReadTimeout(diagnostic, request=request),
            "qdrant_timeout": ResponseHandlingException(httpx.ReadTimeout(diagnostic)),
            "qdrant_gateway": UnexpectedResponse(502, "Bad Gateway", diagnostic.encode(), httpx.Headers()),
            "credentials": httpx.HTTPStatusError(diagnostic, request=request, response=httpx.Response(401, request=request)),
        }[failure]
        error = ProviderError(diagnostic)
        error.__cause__ = cause
    provider.search.side_effect = error
    response = await client.post("/papers/search", json={"query": "failure"})
    assert response.status_code == expected_status
    assert response.json()["code"] == expected_status
    assert message in response.json()["msg"]
    assert response.json()["data"] is None
    assert "internal-stage" not in response.text and "upstream.test" not in response.text
    assert any(fields.get("exc") is error for fields in audited)


@pytest.mark.asyncio
async def test_http_partial_lookup_has_only_unrecovered_item_limitations(scholar_http):
    from scholar.domain.errors import ProviderError

    client, _, provider = scholar_http
    found = provider.lookup.return_value[0].papers[0]
    request = httpx.Request("GET", "https://upstream.test/private-stage")
    error = ProviderError("private stage diagnostic")
    error.__cause__ = httpx.HTTPStatusError("quota", request=request, response=httpx.Response(429, request=request))
    provider.lookup.return_value = [PaperLookupMatch([found]), PaperLookupMatch([], error)]
    response = await client.post("/papers/lookup", json={"identifiers": ["1706.03762", "1706.03763", "1706.03763"]})
    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data) == {"items"}
    assert [item["status"] for item in data["items"]] == ["found", "failed", "failed"]
    assert data["items"][0]["limitations"] == []
    assert data["items"][1]["limitations"] == [{"source": "arxiv", "reason": "rate_limited"}]
    assert data["items"][2] == data["items"][1]
    assert "private" not in response.text

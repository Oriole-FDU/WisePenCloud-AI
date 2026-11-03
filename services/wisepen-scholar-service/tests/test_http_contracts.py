"""Scholar HTTP 合同：认证、检索投影、缓存和逐项标识结果。"""

import asyncio
from datetime import date
from copy import deepcopy

import httpx
import pytest
from common.web.middleware import SecurityHeaderMiddleware
from dependency_injector import providers
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from scholar.api.endpoints import health, papers
from scholar.api.router import api_router
from scholar.container import container
from scholar.domain.errors import ProviderBusy, ProviderError
from scholar.domain.interfaces.paper_discoverer import PaperDiscovery
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.interfaces.paper_searcher import PaperSearchBatch
from scholar.domain.models.paper import Paper
from scholar.main import setup_exception_handlers
from policy_fakes import make_policy_store
from title_fakes import MemoryRedis
from scholar.core.storage.qdrant.paper_repository import to_paper
from scholar.domain.repositories.local_paper_repository import PaperCandidate

FROM_SOURCE_SECRET = "scholar-test-secret"
AUTHORIZED = {"X-From-Source": FROM_SOURCE_SECRET}
PAPER_A = {
    "arxiv_id": "2201.00001",
    "raw_id": "2201.00001",
    "doi": "10.5555/p1.first",
    "title": "First paper",
    "abstract": "A short abstract.",
    "authors": ["Jane Doe"],
    "categories": ["cs.AI"],
    "submitted_date": "2022-01-03",
    "updated_date": "2022-01-05",
    "versions": [{"version": "v1", "created": "Mon, 03 Jan 2022 00:00:00 GMT"}],
    "journal_ref": None,
    "comments": None,
    "license": None,
    "url": "https://arxiv.org/abs/2201.00001",
    "pdf_url": "https://arxiv.org/pdf/2201.00001",
}
PAPER_AMBIGUOUS_A = {
    **PAPER_A,
    "arxiv_id": "2301.11111",
    "raw_id": "2301.11111",
    "doi": "10.5555/p1.ambiguous",
}
PAPER_AMBIGUOUS_B = {
    **PAPER_A,
    "arxiv_id": "2301.22222",
    "raw_id": "2301.22222",
    "doi": "10.5555/p1.ambiguous",
}


def create_app(*, from_source_secret):
    app = FastAPI()
    app.add_middleware(SecurityHeaderMiddleware, from_source_secret=from_source_secret)
    setup_exception_handlers(app)
    app.include_router(api_router, prefix="/scholar")
    return app


class FakePaperRepository:
    """在现有论文仓储与健康检查边界返回确定结果，不依赖历史 ES 装配。"""

    def __init__(self):
        self.alias_exists = False
        self.checked_aliases = []
        self.info_calls = 0
        self.search_calls = []
        self.lookup_calls = []
        self.lookup_documents = {
            ("arxiv_id", "2201.00001"): [PAPER_A],
            ("doi", "10.5555/p1.first"): [PAPER_A],
            ("doi", "10.5555/p1.ambiguous"): [PAPER_AMBIGUOUS_A, PAPER_AMBIGUOUS_B],
        }
        self.lookup_errors = {("doi", "10.5555/p1.error")}

    async def collection_exists(self, name):
        self.checked_aliases.append(name)
        return self.alias_exists

    async def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return []

    async def match_titles(self, titles, **kwargs):
        return [PaperLookupMatch([]) for _ in titles]

    async def lookup(self, ids):
        self.lookup_calls.append(ids)
        results = []
        for identity in ids:
            key = ("arxiv_id" if identity.kind == "arxiv" else "doi", identity.value)
            if key in self.lookup_errors:
                results.append(PaperLookupMatch([], ProviderError("private lookup diagnostic")))
            else:
                results.append(PaperLookupMatch([to_paper(document) for document in self.lookup_documents.get(key, [])]))
        return results


class FakeRedis(MemoryRedis):
    """提供请求级缓存命中和 Redis ping 的可观察行为。"""

    def __init__(self):
        super().__init__()
        self.ping_calls = 0

    async def ping(self):
        self.ping_calls += 1
        return True

class MissingPapers:
    async def lookup(self, ids, *, api_key=None):
        return [PaperLookupMatch([]) for _ in ids]


@pytest.fixture
def scholar_client():
    es, redis = FakePaperRepository(), FakeRedis()
    container.reset_singletons()
    container.policy_store.override(providers.Object(make_policy_store()))
    container.config.from_dict(
        {
            "SCHOLAR_COLLECTION_ALIAS": "test-scholar-papers",
            "FROM_SOURCE_SECRET": FROM_SOURCE_SECRET,
            "SCHOLAR_EMBEDDING_TIMEOUT_SECONDS": 0.1,
            "SCHOLAR_QUERY_CACHE_TTL_SECONDS": 60,
            "SCHOLAR_DISCOVERY_CACHE_TTL_SECONDS": 900,
            "SCHOLAR_REQUEST_TIMEOUT_SECONDS": 3,
            "HF_TOKEN": None,
            "OPENALEX_API_KEY": None,
            "S2_API_KEY": "test-s2-key",
            "UNPAYWALL_EMAIL": "jzsun24@m.fudan.edu.cn",
            "SCHOLAR_HF_CONCURRENCY": 4,
            "SCHOLAR_S2_MAX_QUEUED_AHEAD": 3,
            "SCHOLAR_S2_NOKEY_INTERVAL_SECONDS": 1.5,
            "SCHOLAR_S2_KEY_INTERVAL_SECONDS": 1.5,
            "SCHOLAR_S2_BATCH_MAX_IDS": 300,
            "SCHOLAR_PAPER_CACHE_TTL_SECONDS": 86400,
        }
    )
    from unittest.mock import AsyncMock

    container.embedding_client.override(
        providers.Object(AsyncMock(embed=AsyncMock(return_value=[[0.0] * 768])))
    )
    container.paper_repository.override(providers.Object(es))
    container.qdrant.override(providers.Object(es))
    container.redis.override(providers.Object(redis))
    container.s2_provider.override(providers.Object(MissingPapers()))
    openalex_http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404))
    )
    container.openalex_http.override(providers.Object(openalex_http))
    huggingface_http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    )
    container.huggingface_http.override(providers.Object(huggingface_http))
    unpaywall_http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    container.unpaywall_http.override(providers.Object(unpaywall_http))
    container.wire(modules=[health, papers])
    try:
        with TestClient(create_app(from_source_secret=FROM_SOURCE_SECRET)) as client:
            yield client, es, redis
            client.portal.call(openalex_http.aclose)
            client.portal.call(huggingface_http.aclose)
            client.portal.call(unpaywall_http.aclose)
    finally:
        container.policy_store.reset_last_overriding()
        container.unwire()
        container.embedding_client.reset_override()
        container.paper_repository.reset_override()
        container.qdrant.reset_override()
        container.redis.reset_override()
        container.s2_provider.reset_override()
        container.openalex_http.reset_override()
        container.huggingface_http.reset_override()
        container.unpaywall_http.reset_override()
        container.reset_singletons()


def test_discovery_accepts_non_cs_field_and_reports_unavailable_capability(scholar_client):
    client, _, _ = scholar_client
    response = client.post(
        "/scholar/papers/discover", headers=AUTHORIZED, json={"field": "medicine"}
    )
    assert response.status_code == 503


def test_search_sends_domain_and_inclusive_date_bounds_to_repository(scholar_client):
    client, es, _ = scholar_client
    response = client.post(
        "/scholar/papers/search",
        headers=AUTHORIZED,
        json={
            "query": "  graph models  ",
            "field": "computer_science",
            "source_scope": "multi_source",
            "published_after": "2023-01-01",
            "published_before": "2023-12-31",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["papers"] == []
    call = es.search_calls[0]
    assert call["categories"] == ["cs.*"]
    assert call["published_after"] == date(2023, 1, 1)
    assert call["published_before"] == date(2023, 12, 31)


def test_search_cache_reuses_identical_request_and_isolates_changed_inputs(
    scholar_client,
):
    client, es, _ = scholar_client
    request = {"query": "transformer"}

    first = client.post("/scholar/papers/search", headers=AUTHORIZED, json=request)
    first_searches = len(es.search_calls)
    repeated = client.post("/scholar/papers/search", headers=AUTHORIZED, json=request)
    assert len(es.search_calls) == first_searches
    changed_query = client.post(
        "/scholar/papers/search", headers=AUTHORIZED, json={"query": "graph"}
    )
    changed_field = client.post(
        "/scholar/papers/search",
        headers=AUTHORIZED,
        json={
            "query": "transformer",
            "field": "computer_science",
            "source_scope": "multi_source",
        },
    )

    assert [
        first.status_code,
        repeated.status_code,
        changed_query.status_code,
        changed_field.status_code,
    ] == [200, 200, 200, 200]


def test_lookup_preserves_input_order_duplicates_and_per_identifier_status(
    scholar_client,
):
    client, es, _ = scholar_client
    response = client.post(
        "/scholar/papers/lookup",
        headers={**AUTHORIZED, "X-OpenAlex-Api-Key": "lookup-key"},
        json={
            "identifiers": [
                "https://arxiv.org/abs/2201.00001v2",
                "10.5555/P1.FIRST",
                "arxiv:2201.00001",
                "not-a-paper",
                "openalex:W123",
                "10.5555/p1.ambiguous",
                "2202.99999",
            ]
        },
    )

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [item["identifier"] for item in items] == [
        "https://arxiv.org/abs/2201.00001v2",
        "10.5555/P1.FIRST",
        "arxiv:2201.00001",
        "not-a-paper",
        "openalex:W123",
        "10.5555/p1.ambiguous",
        "2202.99999",
    ]
    assert [item["status"] for item in items] == [
        "found",
        "found",
        "found",
        "not_found",
        "not_found",
        "ambiguous",
        "not_found",
    ]
    assert [item["paper"]["arxiv_id"] for item in items[:3]] == ["2201.00001"] * 3

    assert [(identity.kind, identity.value) for identity in es.lookup_calls[0]] == [
        ("arxiv", "2201.00001"),
        ("doi", "10.5555/p1.first"),
        ("arxiv", "not-a-paper"),
        ("doi", "10.5555/p1.ambiguous"),
        ("arxiv", "2202.99999"),
    ]


def test_lookup_keeps_successes_when_one_item_fails(scholar_client):
    client, _, _ = scholar_client
    response = client.post(
        "/scholar/papers/lookup",
        headers=AUTHORIZED,
        json={"identifiers": ["2201.00001", "10.5555/p1.error"]},
    )

    assert response.status_code == 200
    result = response.json()["data"]
    assert result["items"][0]["status"] == "found"
    assert result["items"][0]["paper"]["arxiv_id"] == "2201.00001"
    assert result["items"][1]["status"] == "failed"
    assert result["items"][1]["limitations"] == [
        {"source": "arxiv", "reason": "source_unavailable"},
        {"source": "openalex", "reason": "credentials_required"},
    ]


def test_lookup_whole_failure_keeps_diagnostics_in_audit(scholar_client):
    client, _, _ = scholar_client
    response = client.post(
        "/scholar/papers/lookup",
        headers=AUTHORIZED,
        json={"identifiers": ["10.5555/p1.error"]},
    )

    assert response.status_code == 503
    assert "private lookup diagnostic" not in response.json()["msg"]
    assert response.json()["data"] is None


@pytest.mark.parametrize("identifier", ["2201.00001", "corpusid:123"])
def test_lookup_keeps_base_facts_or_raises_when_s2_fails(
    scholar_client, identifier
):
    client, _, _ = scholar_client

    class LimitedProvider:
        async def lookup(self, ids, *, api_key=None):
            request = httpx.Request("POST", "https://api.semanticscholar.org/graph/v1/paper/batch")
            error = httpx.HTTPStatusError("429 Too Many Requests from Semantic Scholar", request=request, response=httpx.Response(429, request=request))
            raise ProviderError(str(error)) from error

    with container.s2_provider.override(providers.Object(LimitedProvider())):
        response = client.post(
            "/scholar/papers/lookup",
            headers=AUTHORIZED,
            json={"identifiers": [identifier]},
        )

    if identifier == "2201.00001":
        assert response.status_code == 200
        item = response.json()["data"]["items"][0]
        assert item["paper"]["title"] == "First paper"
        assert item["limitations"] == []
    else:
        assert response.status_code == 429
        assert response.json()["code"] == 429
        assert "rate limited" in response.json()["msg"]
        assert response.json()["data"] is None


@pytest.mark.parametrize("failure", ["deadline", "bad_gateway", "read_timeout"])
def test_search_reports_actual_failure_status_without_stage(scholar_client, failure):
    client, _, _ = scholar_client

    class SlowVectors(FakePaperRepository):
        async def search(self, **kwargs):
            if failure == "bad_gateway":
                raise UnexpectedResponse(502, "Bad Gateway", b"proxy failed", httpx.Headers())
            if failure == "read_timeout":
                raise ResponseHandlingException(httpx.ReadTimeout("vector query timed out"))
            await asyncio.Event().wait()

    with (
        container.paper_repository.override(providers.Object(SlowVectors())),
        container.config.SCHOLAR_REQUEST_TIMEOUT_SECONDS.override(0.03),
    ):
        response = client.post("/scholar/papers/search", headers=AUTHORIZED, json={"query": "slow vectors"})

    status = 502 if failure == "bad_gateway" else 504
    assert response.status_code == status
    assert response.json()["code"] == status
    assert "arxiv" not in response.json()["msg"]
    assert "qdrant.search" not in response.json()["msg"]


def test_search_keeps_found_papers_when_enrichment_exhausts_deadline(scholar_client, monkeypatch):
    client, es, _ = scholar_client

    async def found(**kwargs):
        return [PaperCandidate(to_paper(PAPER_A), 1.0)]

    class SlowEnrichment:
        async def lookup(self, ids, *, api_key=None):
            await asyncio.Event().wait()

    monkeypatch.setattr(es, "search", found)
    with (
        container.policy_store.override(providers.Object(make_policy_store(common_enrichers=["s2"]))),
        container.s2_provider.override(providers.Object(SlowEnrichment())),
        container.config.SCHOLAR_REQUEST_TIMEOUT_SECONDS.override(0.03),
    ):
        response = client.post("/scholar/papers/search", headers=AUTHORIZED, json={"query": "found before enrichment"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["papers"][0]["arxiv_id"] == PAPER_A["arxiv_id"]
    assert data["limitations"] == []


def test_lookup_queue_admission_rejection_is_429(scholar_client):
    client, _, _ = scholar_client

    class BusyProvider:
        async def lookup(self, ids, *, api_key=None):
            raise ProviderBusy(4, 3)

    with container.s2_provider.override(providers.Object(BusyProvider())):
        response = client.post("/scholar/papers/lookup", headers=AUTHORIZED, json={"identifiers": ["corpusid:123"]})

    assert response.status_code == 429
    assert response.json()["code"] == 429
    assert "rate limited" in response.json()["msg"]


def test_multisource_without_openalex_key_keeps_arxiv_success(scholar_client):
    client, _, _ = scholar_client
    response = client.post(
        "/scholar/papers/search",
        headers=AUTHORIZED,
        json={"query": "graph models", "source_scope": "multi_source"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["papers"] == []  # 正常空召回仍是来源成功。


@pytest.mark.parametrize("abstract", ["The original abstract", None])
def test_hf_supplies_paper_when_general_sources_fail_and_reuses_cache(scholar_client, abstract):
    client, _, _ = scholar_client
    requests = []

    class LimitedProvider:
        async def lookup(self, ids, *, api_key=None):
            raise ProviderError("429 Too Many Requests from Semantic Scholar")

    def hf_response(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "2501.12948",
                "title": "HF fallback title",
                "summary": abstract,
                "ai_keywords": ["reinforcement learning", "multi-stage training", "cold-start data", "Qwen", "Llama"],
                "githubRepo": "https://github.com/deepseek-ai/deepseek-r1",
                "githubStars": 91977,
                "linkedModels": [{"id": "deepseek-ai/DeepSeek-R1", "likes": 14415}],
                "linkedDatasets": [{"id": "evaluation/reasoning", "likes": 0}],
                "authors": [{"name": "First Author"}, {"name": "Second Author"}],
                "publishedAt": "2025-01-22T00:00:00Z",
                "ai_summary": "HF summary",
                "upvotes": 0,
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(hf_response))
    with (
        container.s2_provider.override(providers.Object(LimitedProvider())),
        container.huggingface_http.override(providers.Object(http)),
    ):
        responses = [
            client.post(
                "/scholar/papers/lookup",
                headers=AUTHORIZED,
                json={
                    "identifiers": ["2501.12948"],
                    "field": "computer_science",
                },
            )
            for _ in range(2)
        ]
        client.portal.call(http.aclose)

    assert [response.status_code for response in responses] == [200, 200]
    for response in responses:
        result = response.json()["data"]
        item = result["items"][0]
        assert set(item) == {"identifier", "status", "paper", "limitations"}
        assert item["status"] == "found"
        expected_metadata = {
            "upvotes": 0, "ai_keywords": ["reinforcement learning", "multi-stage training", "cold-start data", "Qwen", "Llama"],
            "github_repo": "https://github.com/deepseek-ai/deepseek-r1", "github_stars": 91977,
            "linked_models": [{"id": "deepseek-ai/DeepSeek-R1", "likes": 14415}],
            "linked_datasets": [{"id": "evaluation/reasoning", "likes": 0}],
        }
        if abstract is None:
            expected_metadata["ai_summary"] = "HF summary"
        assert item["paper"]["metadata"]["huggingface"] == expected_metadata
        assert item["paper"]["title"] == "HF fallback title"
        assert item["paper"]["abstract"] == abstract
        assert item["paper"]["authors"] == ["First Author et al."]
    assert len(requests) == 1
    assert requests[0].url.path == "/api/papers/2501.12948"


def test_health_reports_503_until_alias_is_published(scholar_client):
    client, es, redis = scholar_client
    response = client.get("/scholar/health", headers=AUTHORIZED)

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}
    assert es.checked_aliases == ["test-scholar-papers"]
    assert redis.ping_calls == 1


def test_common_from_source_header_blocks_missing_and_invalid_tokens(scholar_client):
    client, es, redis = scholar_client
    missing = client.get("/scholar/health")
    invalid = client.get("/scholar/health", headers={"X-From-Source": "wrong-token"})

    assert missing.status_code == 404
    assert invalid.status_code == 404
    assert es.info_calls == 0
    assert redis.ping_calls == 0


def test_lookup_preserves_selected_offline_s2_facts(scholar_client):
    client, es, _ = scholar_client
    s2 = {
        "corpus_id": "1001",
        "citation_count": 0,
        "influential_citation_count": 0,
        "is_open_access": False,
        "tldr": {"model": "tldr@v2.0.0", "text": ""},
        "release": "declared-test-release",
    }
    es.lookup_documents[("arxiv_id", "2201.00001")] = [
        {**PAPER_A, "metadata": {"s2": s2}}
    ]
    response = client.post(
        "/scholar/papers/lookup",
        headers=AUTHORIZED,
        json={"identifiers": ["arxiv:2201.00001"]},
    )
    assert response.json()["data"]["items"][0]["paper"]["metadata"]["s2"] == {
        "corpus_id": "1001",
        "citation_count": 0,
        "influential_citation_count": 0,
    }
    assert es.lookup_documents[("arxiv_id", "2201.00001")][0]["metadata"]["s2"] == s2


def test_search_and_lookup_share_redis_cache_across_users_and_credentials(scholar_client):
    client, _, redis = scholar_client

    class RecordingSearcher:
        def __init__(self):
            self.calls = []
            self.lookup_calls = []

        async def search(self, **kwargs):
            self.calls.append(kwargs)
            return PaperSearchBatch([])

        async def lookup(self, ids, *, api_key=None):
            self.lookup_calls.append((ids, api_key))
            return [PaperLookupMatch([]) for _ in ids]

    searcher = RecordingSearcher()
    request = {"query": "clinical trials", "field": "medicine"}
    headers = {**AUTHORIZED, "X-OpenAlex-Api-Key": "user-secret"}
    with container.openalex_searcher.override(providers.Object(searcher)):
        first = client.post(
            "/scholar/papers/search",
            headers={**headers, "X-User-Id": "user-a"},
            json=request,
        )
        repeated = client.post(
            "/scholar/papers/search",
            headers={**headers, "X-User-Id": "user-a"},
            json=request,
        )
        another_user = client.post(
            "/scholar/papers/search",
            headers={**headers, "X-User-Id": "user-b"},
            json=request,
        )
        changed_key = client.post(
            "/scholar/papers/search",
            headers={
                **AUTHORIZED,
                "X-OpenAlex-Api-Key": "another-secret",
                "X-User-Id": "user-a",
            },
            json=request,
        )
        without_key = client.post(
            "/scholar/papers/search",
            headers={**AUTHORIZED, "X-User-Id": "user-c"},
            json=request,
        )
        lookup = client.post(
            "/scholar/papers/lookup",
            headers={**headers, "X-User-Id": "user-a"},
            json={"identifiers": ["openalex:W123"]},
        )
        shared_lookup = client.post(
            "/scholar/papers/lookup",
            headers={**AUTHORIZED, "X-OpenAlex-Api-Key": "another-secret"},
            json={"identifiers": ["openalex:W123"]},
        )
        lookup_without_key = client.post(
            "/scholar/papers/lookup",
            headers=AUTHORIZED,
            json={"identifiers": ["openalex:W123"]},
        )

    assert first.status_code == 200
    for response in (repeated, another_user, changed_key):
        assert response.status_code == 200
    assert without_key.status_code == 503
    assert without_key.json()["code"] == 503
    assert "BYOK" in without_key.json()["msg"]
    assert len(searcher.calls) == 1
    assert searcher.calls[0]["api_key"] == "user-secret"
    assert searcher.calls[0]["field"].value == "medicine"
    assert lookup.status_code == 200
    assert lookup.json()["data"]["items"][0]["status"] == "not_found"
    for response in (shared_lookup, lookup_without_key):
        assert response.status_code == 200
        assert response.json()["data"]["items"][0]["status"] == "not_found"
    assert len(searcher.lookup_calls) == 1
    assert searcher.lookup_calls[0][1] == "user-secret"
    assert "user-secret" not in str(redis.values)
    assert "another-secret" not in str(redis.values)


def test_discovery_shares_redis_cache_across_credentials_and_preserves_window(scholar_client):
    client, _, redis = scholar_client

    class DailyPapers:
        def __init__(self):
            self.calls = []

        async def discover(self, **kwargs):
            self.calls.append(kwargs)
            return PaperDiscovery([], date(2026, 10, 26), date(2026, 10, 26))

    provider = DailyPapers()
    payload = {"field": "computer_science", "limit": 2}
    with container.huggingface_provider.override(providers.Object(provider)):
        first = client.post(
            "/scholar/papers/discover",
            headers={**AUTHORIZED, "X-HuggingFace-Api-Key": "user-secret"},
            json=payload,
        )
        changed_key = client.post(
            "/scholar/papers/discover",
            headers={**AUTHORIZED, "X-HuggingFace-Api-Key": "another-secret"},
            json=payload,
        )
        repeated = client.post(
            "/scholar/papers/discover", headers=AUTHORIZED, json=payload
        )
        invalid = client.post(
            "/scholar/papers/discover",
            headers=AUTHORIZED,
            json={**payload, "query": "LLM"},
        )

    assert first.status_code == 200
    assert first.json()["data"]["window_start"] == "2026-10-26"
    for response in (changed_key,):
        assert response.status_code == 200
        assert response.json()["data"]["window_start"] == "2026-10-26"
    assert repeated.status_code == 503
    assert "BYOK" in repeated.json()["msg"]
    assert len(provider.calls) == 1
    assert provider.calls[0]["api_key"] == "user-secret"
    assert "user-secret" not in str(redis.values)
    assert "another-secret" not in str(redis.values)
    assert invalid.status_code == 400


@pytest.mark.parametrize("operation", ["search", "lookup", "discover"])
@pytest.mark.parametrize("pdf_url", ["https://repository.test/paper.pdf", None])
def test_pdf_recovery_is_shared_by_public_exits_and_preserves_cached_candidates(scholar_client, operation, pdf_url):
    client, _, redis = scholar_client
    candidates = [Paper(
        arxiv_id=None, doi=f"10.1234/paper{i}", title=f"Paper {i}", abstract="Abstract",
        authors=["First", "Second"], publication_date=None, year=2026,
        url=f"https://publisher.test/paper{i}", pdf_url=None, source="s2",
        metadata={"s2": {"citation_count": 0, "is_open_access": False}},
    ) for i in range(3)]
    original = deepcopy(candidates)

    class Papers:
        async def search(self, **kwargs):
            assert kwargs["api_key"] == "user-oa"
            return PaperSearchBatch(candidates)

        async def lookup(self, ids, *, api_key=None):
            return [PaperLookupMatch([candidates[0]]) for _ in ids]

        async def discover(self, **kwargs):
            assert kwargs["api_key"] == "user-hf"
            return PaperDiscovery(candidates, None, None)

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"best_oa_location": {"url_for_pdf": pdf_url}, "oa_locations": []})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    upstream = providers.Object(Papers())
    payload = {
        "search": {"query": "clinical", "field": "medicine", "limit": 1},
        "lookup": {"identifiers": ["10.1234/paper0", "10.1234/paper0"]},
        "discover": {"field": "computer_science", "limit": 1},
    }[operation]
    headers = {**AUTHORIZED, "X-OpenAlex-Api-Key": "user-oa", "X-HuggingFace-Api-Key": "user-hf"}
    with container.openalex_searcher.override(upstream), container.s2_provider.override(upstream), container.huggingface_provider.override(upstream), container.unpaywall_http.override(providers.Object(http)):
        first = client.post(f"/scholar/papers/{operation}", headers=headers, json=payload)
        repeated = client.post(f"/scholar/papers/{operation}", headers=headers, json=payload)
        client.portal.call(http.aclose)

    assert first.status_code == repeated.status_code == 200
    data = first.json()["data"]
    papers = [item["paper"] for item in data["items"]] if operation == "lookup" else data["papers"]
    assert all(paper["pdf_url"] == pdf_url for paper in papers)
    assert all(paper["metadata"] == {"s2": {"citation_count": 0}} for paper in papers)
    assert len(requests) == 1
    assert requests[0].url.path == "/v2/10.1234/paper0"
    assert candidates == original
    assert sum("unpaywall" in key for key in redis.values) == 1


def test_s2_byok_overrides_platform_only_for_current_http_call(scholar_client):
    client, _, _ = scholar_client
    keys = []

    class Details:
        async def lookup(self, ids, *, api_key=None):
            keys.append(api_key)
            return [PaperLookupMatch([Paper(
                arxiv_id=identity.value, doi=None, title="Found paper", abstract=None,
                authors=[], publication_date=None, year=None, url="https://s2.test/paper",
                pdf_url=None, source="s2", metadata={"s2": {"citation_count": 0}},
            )]) for identity in ids]

    with container.s2_provider.override(providers.Object(Details())):
        for value, headers in [
            ("2401.00001", {**AUTHORIZED, "X-S2-Api-Key": "user-s2"}),
            ("2401.00002", AUTHORIZED),
        ]:
            response = client.post("/scholar/papers/lookup", headers=headers, json={"identifiers": [value]})
            assert response.status_code == 200
    assert keys == ["user-s2", "test-s2-key"]


@pytest.mark.parametrize("user_key", [None, "user-hf"])
def test_hf_search_requires_byok_while_local_results_can_use_platform_enrichment(scholar_client, user_key):
    client, _, _ = scholar_client
    local = Paper("2401.00001", None, "Local paper", "Abstract", ["Author"], None, 2026,
                  "https://arxiv.org/abs/2401.00001", None, "arxiv", {"arxiv": {"categories": ["cs.AI"]}})
    search_keys, detail_keys = [], []

    class Local:
        cache_revision = "test"

        async def search(self, **kwargs):
            return PaperSearchBatch([local])

        async def match_titles(self, **kwargs):
            return PaperSearchBatch([])

    class HuggingFace:
        async def search(self, **kwargs):
            search_keys.append(kwargs["api_key"])
            return PaperSearchBatch([])

        async def lookup(self, ids, *, api_key=None):
            detail_keys.append(api_key)
            result = deepcopy(local)
            result.metadata = {"huggingface": {"upvotes": 11}}
            return [PaperLookupMatch([result]) for _ in ids]

    headers = {**AUTHORIZED, **({"X-HuggingFace-Api-Key": user_key} if user_key else {})}
    with container.arxiv_searcher.override(providers.Object(Local())), container.huggingface_provider.override(providers.Object(HuggingFace())), container.platform_credentials.override(providers.Object({"huggingface": "platform-hf"})):
        response = client.post("/scholar/papers/search", headers=headers, json={
            "query": "attention", "field": "computer_science", "source_scope": "multi_source",
        })

    assert response.status_code == 200
    assert search_keys == (["user-hf"] if user_key else [])
    assert detail_keys == [user_key or "platform-hf"]
    limitations = response.json()["data"]["limitations"]
    assert any(item == {"source": "huggingface", "reason": "credentials_required"} for item in limitations) == (user_key is None)

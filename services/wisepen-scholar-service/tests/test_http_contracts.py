"""Scholar HTTP 合同：认证、检索投影、缓存和逐项标识结果。"""

from datetime import date

import pytest
from dependency_injector import providers
from fastapi.testclient import TestClient

from scholar.api.app import create_app
from scholar.api.endpoints import discovery, health, papers
from scholar.container import container
from scholar.domain.interfaces.paper_discoverer import PaperDiscovery
from scholar.domain.interfaces.provider_error import ProviderError

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


class FakeIndices:
    """模拟健康检查所需的索引别名查询。"""

    def __init__(self, es):
        self.es = es

    async def exists_alias(self, *, name):
        self.es.checked_aliases.append(name)
        return self.es.alias_exists


class FakeElasticsearch:
    """在 Scholar 与 ES 的调用边界记录 DSL 并返回确定响应。"""

    def __init__(self):
        self.alias_exists = False
        self.checked_aliases = []
        self.info_calls = 0
        self.search_calls = []
        self.msearch_calls = []
        self.lookup_documents = {
            ("arxiv_id", "2201.00001"): [PAPER_A],
            ("doi", "10.5555/p1.first"): [PAPER_A],
            ("doi", "10.5555/p1.ambiguous"): [PAPER_AMBIGUOUS_A, PAPER_AMBIGUOUS_B],
        }
        self.lookup_errors = {("doi", "10.5555/p1.error")}
        self.indices = FakeIndices(self)

    async def info(self):
        self.info_calls += 1
        return {"name": "fake"}

    async def search(self, *, index, size, track_total_hits, query):
        self.search_calls.append({"index": index, "query": query})
        return {"hits": {"hits": []}}

    async def msearch(self, *, searches):
        self.msearch_calls.append(searches)
        responses = []
        for position in range(1, len(searches), 2):
            field, value = next(iter(searches[position]["query"]["term"].items()))
            if (field, value) in self.lookup_errors:
                responses.append(
                    {"error": {"type": "search_phase_execution_exception"}}
                )
                continue
            documents = self.lookup_documents.get((field, value), [])
            responses.append(
                {"hits": {"hits": [{"_source": document} for document in documents]}}
            )
        return {"responses": responses}


class FakeRedis:
    """提供请求级缓存命中和 Redis ping 的可观察行为。"""

    def __init__(self):
        self.values = {}
        self.ping_calls = 0

    async def get(self, key):
        return self.values.get(key)

    async def mget(self, keys):
        return [self.values.get(key) for key in keys]

    async def set(self, key, value, *, ex):
        self.values[key] = value

    async def ping(self):
        self.ping_calls += 1
        return True


@pytest.fixture
def scholar_client():
    es, redis = FakeElasticsearch(), FakeRedis()
    container.reset_singletons()
    container.config.from_dict(
        {
            "SCHOLAR_INDEX_ALIAS": "test-scholar-papers",
            "SCHOLAR_QUERY_CACHE_TTL_SECONDS": 60,
            "SCHOLAR_DISCOVERY_CACHE_TTL_SECONDS": 900,
            "SCHOLAR_REQUEST_TIMEOUT_SECONDS": 3,
            "HF_TOKEN": None,
            "S2_API_KEY": "test-s2-key",
            "SCHOLAR_HF_CONCURRENCY": 4,
            "SCHOLAR_PAPER_CACHE_TTL_SECONDS": 86400,
            "SCHOLAR_NEGATIVE_CACHE_TTL_SECONDS": 3600,
        }
    )
    container.elasticsearch.override(providers.Object(es))
    container.redis.override(providers.Object(redis))
    container.wire(modules=[health, papers, discovery])
    try:
        with TestClient(create_app(from_source_secret=FROM_SOURCE_SECRET)) as client:
            yield client, es, redis
    finally:
        container.unwire()
        container.elasticsearch.reset_override()
        container.redis.reset_override()
        container.reset_singletons()


def test_search_sends_domain_and_inclusive_date_bounds_to_es(scholar_client):
    client, es, _ = scholar_client
    response = client.post(
        "/scholar/papers/search",
        headers=AUTHORIZED,
        json={
            "query": "  graph models  ",
            "field": "computer_science",
            "published_after": "2023-01-01",
            "published_before": "2023-12-31",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["papers"] == []
    query = es.search_calls[0]["query"]["bool"]
    assert query["must"][0]["combined_fields"]["query"] == "graph models"
    assert {
        "bool": {
            "should": [{"wildcard": {"categories": "cs.*"}}],
            "minimum_should_match": 1,
        }
    } in query["filter"]
    assert {
        "range": {"submitted_date": {"gte": "2023-01-01", "lte": "2023-12-31"}}
    } in query["filter"]


def test_search_cache_reuses_identical_request_and_isolates_changed_inputs(
    scholar_client,
):
    client, es, _ = scholar_client
    request = {"query": "transformer"}

    first = client.post("/scholar/papers/search", headers=AUTHORIZED, json=request)
    repeated = client.post("/scholar/papers/search", headers=AUTHORIZED, json=request)
    changed_query = client.post(
        "/scholar/papers/search", headers=AUTHORIZED, json={"query": "graph"}
    )
    changed_field = client.post(
        "/scholar/papers/search",
        headers=AUTHORIZED,
        json={"query": "transformer", "field": "computer_science"},
    )

    assert [
        first.status_code,
        repeated.status_code,
        changed_query.status_code,
        changed_field.status_code,
    ] == [200, 200, 200, 200]
    assert first.json()["data"]["execution"][0]["cache_hit"] is False
    assert repeated.json()["data"]["execution"][0]["cache_hit"] is True
    assert changed_query.json()["data"]["execution"][0]["cache_hit"] is False
    assert changed_field.json()["data"]["execution"][0]["cache_hit"] is False
    assert len(es.search_calls) == 3


def test_lookup_preserves_input_order_duplicates_and_per_identifier_status(
    scholar_client,
):
    client, es, _ = scholar_client
    response = client.post(
        "/scholar/papers/lookup",
        headers=AUTHORIZED,
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
        "invalid_identifier",
        "missing_api_key",
        "ambiguous",
        "not_found",
    ]
    assert [item["paper"]["arxiv_id"] for item in items[:3]] == ["2201.00001"] * 3

    searches = es.msearch_calls[0]
    assert [
        searches[position]["query"]["term"] for position in range(1, len(searches), 2)
    ] == [
        {"arxiv_id": "2201.00001"},
        {"doi": "10.5555/p1.first"},
        {"doi": "10.5555/p1.ambiguous"},
        {"arxiv_id": "2202.99999"},
    ]


def test_lookup_keeps_successes_when_one_msearch_item_fails(scholar_client):
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
    assert "search_phase_execution_exception" in result["items"][1]["error"]
    assert result["execution"][0]["status"] == "partial"


def test_lookup_whole_failure_exposes_original_error_to_the_caller(scholar_client):
    client, _, _ = scholar_client
    response = client.post(
        "/scholar/papers/lookup",
        headers=AUTHORIZED,
        json={"identifiers": ["10.5555/p1.error"]},
    )

    assert response.status_code == 503
    assert "search_phase_execution_exception" in response.json()["msg"]
    assert response.json()["data"] is None


@pytest.mark.parametrize("identifier", ["2201.00001", "corpusid:123"])
def test_full_lookup_keeps_base_facts_or_raises_when_s2_fails(
    scholar_client, identifier
):
    client, _, _ = scholar_client

    class LimitedEnricher:
        async def enrich(self, identifiers, *, include_details=False):
            raise ProviderError("429 Too Many Requests from Semantic Scholar")

    with container.s2_provider.override(providers.Object(LimitedEnricher())):
        response = client.post(
            "/scholar/papers/lookup",
            headers=AUTHORIZED,
            json={"identifiers": [identifier], "purpose": "full"},
        )

    if identifier == "2201.00001":
        assert response.status_code == 200
        item = response.json()["data"]["items"][0]
        assert item["paper"]["title"] == "First paper"
        assert item["enrichment_status"] == "failed"
        assert "429" in item["error"]
    else:
        assert response.status_code == 503
        assert "429" in response.json()["msg"]
        assert response.json()["data"] is None


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/scholar/papers/search",
            {"query": "graph models", "source_scope": "multi_source"},
        ),
    ],
)
def test_schema_accepts_unavailable_capabilities_and_preserves_business_code(
    scholar_client, path, payload
):
    client, _, _ = scholar_client
    response = client.post(path, headers=AUTHORIZED, json=payload)

    assert response.status_code == 200
    assert response.json()["code"] == 503
    assert response.json()["data"] is None


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
    assert response.json()["data"]["items"][0]["paper"]["metadata"]["s2"] == s2


def test_search_uses_openalex_header_and_user_scoped_cache(scholar_client):
    client, _, redis = scholar_client

    class RecordingSearcher:
        def __init__(self):
            self.calls = []
            self.lookup_calls = []

        async def search(self, **kwargs):
            self.calls.append(kwargs)
            return []

        async def lookup(self, identifier, *, api_key):
            self.lookup_calls.append((identifier, api_key))

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
        lookup = client.post(
            "/scholar/papers/lookup",
            headers={**headers, "X-User-Id": "user-a"},
            json={"identifiers": ["openalex:W123"]},
        )

    assert [first.status_code, repeated.status_code, another_user.status_code] == [
        200,
        200,
        200,
    ]
    assert first.json()["data"]["execution"][0]["cache_hit"] is False
    assert repeated.json()["data"]["execution"][0]["cache_hit"] is True
    assert another_user.json()["data"]["execution"][0]["cache_hit"] is False
    assert len(searcher.calls) == 2
    assert all(call["api_key"] == "user-secret" for call in searcher.calls)
    assert all(call["field"].value == "medicine" for call in searcher.calls)
    assert lookup.status_code == 200
    assert lookup.json()["data"]["items"][0]["status"] == "not_found"
    assert len(searcher.lookup_calls) == 1
    assert searcher.lookup_calls[0][1] == "user-secret"
    assert "user-secret" not in str(redis.values)


def test_discovery_uses_registered_provider_and_caches_real_window(scholar_client):
    client, _, _ = scholar_client

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
            "/scholar/papers/discover", headers=AUTHORIZED, json=payload
        )
        repeated = client.post(
            "/scholar/papers/discover", headers=AUTHORIZED, json=payload
        )
        invalid = client.post(
            "/scholar/papers/discover",
            headers=AUTHORIZED,
            json={**payload, "query": "LLM"},
        )

    assert first.status_code == repeated.status_code == 200
    assert first.json()["data"]["window_start"] == "2026-10-26"
    assert repeated.json()["data"]["execution"][0]["cache_hit"]
    assert len(provider.calls) == 1
    assert invalid.status_code == 400

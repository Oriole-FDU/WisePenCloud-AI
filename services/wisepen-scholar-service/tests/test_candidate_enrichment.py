"""从 provider 与公开搜索入口验证候选论文的完整 lookup 结果。"""

from copy import deepcopy
from dataclasses import replace
from datetime import date

import httpx
import pytest
from scholar.application.field_policies import FieldPolicy
from scholar.application.lookup_runner import LookupBinding, LookupRunner
from scholar.application.services.search import (
    PaperSearch,
    PaperSearchService,
    SearchBinding,
)
from scholar.domain.interfaces.paper_searcher import PaperSearchBatch
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import IdentifierKind, parse_identifier
from scholar.domain.models.paper import Paper, PaperSource
from scholar.providers.policies.enrichment import ENRICHMENT_RULES
from scholar.providers.huggingface import HuggingFaceProvider


def make_paper(arxiv_id, *, source=PaperSource.ARXIV, metadata=None):
    return Paper(
        arxiv_id=arxiv_id,
        doi=None,
        title=f"Candidate {arxiv_id}",
        abstract="Candidate abstract",
        authors=["Candidate Author"],
        publication_date=date(2024, 1, 2),
        year=2024,
        url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "https://example.test",
        pdf_url=None,
        source=source,
        metadata={} if metadata is None else metadata,
    )


class FakeQueryCache:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def put(self, key, papers):
        self.values[key] = list(papers)


class FakeLookupCache:
    """按所有身份别名共享 provider 返回的完整论文。"""

    def __init__(self):
        self.values = {}
        self.ttls = []

    async def get_many(self, ids):
        return [self.values.get(identity) for identity in ids]

    async def put_many(self, entries):
        for record, ttl_seconds in entries:
            self.ttls.append(ttl_seconds)
            for identity in record.cache_aliases:
                self.values[identity] = record

    async def invalidate(self, identifiers):
        for identity in identifiers:
            self.values.pop(identity, None)


class FakeSearcher:
    def __init__(self, papers):
        self.papers = papers
        self.calls = []

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        return PaperSearchBatch(list(self.papers))


def hf_payload(arxiv_id):
    return {
        "id": arxiv_id,
        "title": f"HF title {arxiv_id}",
        "publishedAt": "2024-01-02T00:00:00Z",
        "upvotes": 0,
        "githubRepo": "",
        "githubStars": False,
        "linkedModels": [{"id": "org/model", "likes": 0}],
        "linkedDatasets": [],
        "paper": {
            "summary": "HF abstract",
            "authors": [{"name": "First Author"}, {"name": "Second Author"}],
        },
    }


@pytest.mark.asyncio
async def test_huggingface_lookup_returns_complete_paper_and_keeps_404_as_not_found():
    requests = []

    async def handle(request):
        requests.append(request)
        arxiv_id = request.url.path.rsplit("/", 1)[-1]
        if arxiv_id == "2401.00002":
            return httpx.Response(404)
        return httpx.Response(200, json=hf_payload(arxiv_id))

    identities = [parse_identifier(value) for value in ("2401.00001", "2401.00002")]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        matches = await HuggingFaceProvider(http).lookup(identities)

    paper = matches[0].papers[0]
    assert matches[1].papers == []
    assert paper.source == PaperSource.HUGGINGFACE
    assert paper.title == "HF title 2401.00001"
    assert paper.abstract == "HF abstract"
    assert paper.authors == ["First Author", "Second Author"]
    assert paper.metadata["huggingface"]["upvotes"] == 0
    assert paper.metadata["huggingface"]["github_stars"] is False
    assert paper.metadata["huggingface"]["linked_models"] == [
        {"id": "org/model", "likes": 0}
    ]
    assert {request.url.path.rsplit("/", 1)[-1] for request in requests} == {
        "2401.00001",
        "2401.00002",
    }


@pytest.mark.asyncio
async def test_search_merges_hf_facts_in_place_and_reuses_positive_and_negative_cache():
    requests = []

    async def handle(request):
        requests.append(request)
        arxiv_id = request.url.path.rsplit("/", 1)[-1]
        if arxiv_id == "2401.00003":
            return httpx.Response(404)
        return httpx.Response(200, json=hf_payload(arxiv_id))

    candidates = [
        make_paper("2401.00001", metadata={"arxiv": {
            "categories": ["cs.CL"], "raw_id": "2401.00001",
            "license": "internal license fact",
            "versions": [{"version": "v1"}, {"version": "v2"}],
        }}),
        make_paper("2401.00003"),
        make_paper(None),
    ]
    candidates[0].authors.append("Another Author")
    candidates[2].doi = "10.5555/no-arxiv"
    original = deepcopy(candidates)
    query_cache = FakeQueryCache()
    lookup_cache = FakeLookupCache()
    searcher = FakeSearcher(candidates)
    policy = FieldPolicy([PaperSource.ARXIV], [PaperSource.HUGGINGFACE])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        lookup = LookupRunner(
            {
                PaperSource.HUGGINGFACE: LookupBinding(HuggingFaceProvider(http), lookup_cache, replace(ENRICHMENT_RULES["huggingface"], identifier_kinds=[IdentifierKind.ARXIV]))
            }
        )
        service = PaperSearchService(
            {PaperSource.ARXIV: SearchBinding(searcher)},
            {AcademicField.GENERAL: policy},
            query_cache,
            lookup,
        )
        first = await service.search(PaperSearch("candidate lookup"))
        repeated = await service.search(PaperSearch("candidate lookup"))

    assert [paper.arxiv_id for paper in first.papers] == [
        "2401.00001",
        "2401.00003",
        None,
    ]
    assert first.papers[0].title == "Candidate 2401.00001"
    assert first.papers[0].metadata["huggingface"]["upvotes"] == 0
    assert first.papers[0].metadata["huggingface"]["github_stars"] is False
    assert first.papers[0].metadata["arxiv"] == {
        "categories": ["cs.CL"], "latest_version": "v2",
    }
    assert first.papers[0].authors == ["Candidate Author et al."]
    assert repeated.papers == first.papers
    assert first.papers[1].metadata == {}
    assert first.papers[2].metadata == {}
    # 冷查询与命中缓存共用出口投影，缓存及 provider 候选仍保留完整事实。
    assert candidates == original
    assert next(iter(query_cache.values.values())) == original
    assert len(searcher.calls) == 1
    assert {request.url.path.rsplit("/", 1)[-1] for request in requests} == {
        "2401.00001",
        "2401.00003",
    }
    assert set(lookup_cache.ttls) == {3600, 86400}
    assert repeated.enrichment_execution[0].cache_hit is True
    assert len(requests) == 2

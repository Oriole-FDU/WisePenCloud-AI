"""从 provider 与公开搜索入口验证候选论文的完整 lookup 结果。"""
from policy_fakes import make_policy_store

from pdf_fakes import make_pdf_service

from copy import deepcopy
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from scholar.domain.models.fields import FieldPolicy
from scholar.application.lookup_runner import LookupBinding, LookupRunner
from scholar.application.lookup import PaperLookup, PaperLookupService
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.application.search import (
    PaperSearch,
    PaperSearchService,
    SearchBinding,
)
from scholar.domain.interfaces.paper_searcher import PaperSearchBatch
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import IdentifierKind, parse_identifier
from scholar.domain.models.paper import Paper, PaperSource
from scholar.core.providers import PROVIDER_POLICIES
from scholar.core.providers import HuggingFaceProvider


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
                PaperSource.HUGGINGFACE: LookupBinding(HuggingFaceProvider(http), lookup_cache, replace(PROVIDER_POLICIES["huggingface"], identifier_kinds=[IdentifierKind.ARXIV]))
            }
        )
        service = PaperSearchService(
            {PaperSource.ARXIV: SearchBinding(searcher)},
            make_policy_store({AcademicField.GENERAL: policy}),
            query_cache,
            lookup,
            platform_credentials={"huggingface": "platform-hf"},
        pdf=make_pdf_service())
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
        "categories": ["cs.CL"],
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
    assert all(request.headers["authorization"] == "Bearer platform-hf" for request in requests)


@pytest.mark.asyncio
async def test_search_and_lookup_share_arxiv_projection_and_field_enrichment():
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json=hf_payload("2401.00001"))

    base = make_paper("2401.00001", metadata={"arxiv": {
        "categories": ["cs.CL", "cs.AI"],
        "journal_ref": "Proceedings of Example 2024, pp. 1-14",
        "comments": "Accepted at Example; 14 pages",
        "updated_date": "2025-02-03",
        "versions": [{"version": "v1"}, {"version": "v3"}],
    }})
    original = deepcopy(base)
    local = SimpleNamespace(lookup=AsyncMock(return_value=[PaperLookupMatch([base])]))
    policies = make_policy_store({
        AcademicField.COMPUTER_SCIENCE: FieldPolicy(["arxiv"], ["huggingface"]),
    })
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        runner = LookupRunner({
            "arxiv": LookupBinding(local, None, PROVIDER_POLICIES["arxiv"]),
            "huggingface": LookupBinding(HuggingFaceProvider(http), FakeLookupCache(), PROVIDER_POLICIES["huggingface"]),
        })
        search = PaperSearchService(
            {"arxiv": SearchBinding(FakeSearcher([base]))}, policies,
            FakeQueryCache(), runner, make_pdf_service(),
        )
        lookup = PaperLookupService(runner, pdf=make_pdf_service(), policies=policies)
        searched = await search.search(PaperSearch("paper", field=AcademicField.COMPUTER_SCIENCE))
        retrieved = await lookup.lookup(PaperLookup([base.arxiv_id], AcademicField.COMPUTER_SCIENCE))

    assert searched.papers == [retrieved.items[0].paper]
    assert searched.papers[0].metadata["arxiv"] == {
        "categories": ["cs.CL", "cs.AI"],
        "journal_ref": "Proceedings of Example 2024, pp. 1-14",
        "comments": "Accepted at Example; 14 pages",
    }
    assert searched.papers[0].metadata["huggingface"]["linked_models"] == [{"id": "org/model", "likes": 0}]
    assert len(requests) == 1
    assert base == original


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [False, True])
async def test_hf_search_fetches_details_only_when_required_fields_are_missing(complete):
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json=hf_payload("2401.00001"))

    metadata = {"upvotes": 0}
    if complete:
        metadata.update({
            "ai_summary": None, "ai_keywords": [], "github_repo": None,
            "github_stars": None, "linked_models": [{"id": "org/model", "likes": 0}],
            "linked_datasets": [],
        })
    base = make_paper("2401.00001", source=PaperSource.HUGGINGFACE,
                      metadata={"huggingface": metadata})
    original = deepcopy(base)
    policies = make_policy_store({
        AcademicField.COMPUTER_SCIENCE: FieldPolicy(["huggingface"], ["huggingface"]),
    })
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        search = PaperSearchService(
            {"huggingface": SearchBinding(FakeSearcher([base]))}, policies,
            FakeQueryCache(), LookupRunner({
                "huggingface": LookupBinding(HuggingFaceProvider(http), None, PROVIDER_POLICIES["huggingface"]),
            }), make_pdf_service(),
        )
        result = await search.search(PaperSearch("paper", field=AcademicField.COMPUTER_SCIENCE))

    assert result.papers[0].metadata["huggingface"]["linked_models"] == [{"id": "org/model", "likes": 0}]
    assert len(requests) == (0 if complete else 1)
    assert base == original

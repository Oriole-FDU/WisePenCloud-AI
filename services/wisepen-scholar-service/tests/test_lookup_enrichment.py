"""从 lookup 公共入口验证完整论文查询、缓存复用和逐项结果。"""

from pdf_fakes import make_pdf_service

from dataclasses import replace
from copy import deepcopy
from datetime import date

from scholar.providers.policies import PROVIDER_POLICIES

import pytest
from scholar.application.lookup_runner import LookupBinding, LookupRunner
from scholar.application.services.lookup import (
    LookupStatus,
    PaperLookup,
    PaperLookupService,
)
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.identifiers import (
    IdentifierKind,
    parse_identifier,
)
from scholar.domain.models.paper import Paper, PaperSource


def make_paper(
    source=PaperSource.ARXIV,
    *,
    arxiv_id="2401.00001",
    doi=None,
    title="Lookup title",
    authors=None,
    metadata=None,
):
    return Paper(
        arxiv_id=arxiv_id,
        doi=doi,
        title=title,
        abstract="Lookup abstract",
        authors=["Ada Researcher"] if authors is None else authors,
        publication_date=date(2024, 1, 2),
        year=2024,
        url="https://example.test/paper",
        pdf_url="https://example.test/paper.pdf",
        source=source,
        metadata={} if metadata is None else metadata,
    )


class FakeProvider:
    """按公开 lookup 端口返回每个身份的完整基础论文或单项失败。"""

    def __init__(self, matches=None):
        self.matches = matches or {}
        self.calls = []

    async def lookup(self, ids, *, api_key=None):
        ids = list(ids)
        self.calls.append((ids, api_key))
        return [self.matches.get(identity, PaperLookupMatch([])) for identity in ids]


class FakeCache:
    """按论文身份共享完整 Paper，并记录正负缓存的 TTL。"""

    def __init__(self):
        self.values = {}
        self.puts = []

    async def get_many(self, ids):
        return [self.values.get(identity) for identity in ids]

    async def put_many(self, entries):
        self.puts.extend(entries)
        for record, _ttl_seconds in entries:
            for identity in record.cache_aliases:
                self.values[identity] = record

    async def invalidate(self, identifiers):
        for identity in identifiers:
            self.values.pop(identity, None)


def binding(provider, *, cache=None, identifier_kinds=None, **kwargs):
    return LookupBinding(
        provider,
        cache,
        replace(PROVIDER_POLICIES["arxiv"], identifier_kinds=identifier_kinds or IdentifierKind.members(), **({"has_signals": None} | kwargs)),
    )


def service(bindings, *, platform_credentials=None):
    return PaperLookupService(
        LookupRunner(bindings), platform_credentials=platform_credentials
    , pdf=make_pdf_service())


@pytest.mark.asyncio
async def test_lookup_batches_normalized_ids_and_preserves_order_ambiguity_and_item_error():
    arxiv = parse_identifier("2401.00001")
    doi = parse_identifier("10.5555/ambiguous")
    failed = parse_identifier("2401.00002")
    missing = parse_identifier("2401.00003")
    local = make_paper(arxiv_id=arxiv.value)
    provider = FakeProvider(
        {
            arxiv: PaperLookupMatch([local]),
            doi: PaperLookupMatch(
                [
                    make_paper(doi=doi.value, title="First"),
                    make_paper(doi=doi.value, title="Second"),
                ]
            ),
            failed: PaperLookupMatch([], error="msearch item rejected"),
        }
    )
    lookup = service(
        {
            PaperSource.ARXIV: binding(
                provider,
                identifier_kinds=[IdentifierKind.ARXIV, IdentifierKind.DOI],
            )
        }
    )

    result = await lookup.lookup(
        PaperLookup(
            [
                "https://arxiv.org/abs/2401.00001v2",
                "arxiv:2401.00001v1",
                doi.value,
                failed.value,
                missing.value,
            ]
        )
    )

    assert [item.status for item in result.items] == [
        LookupStatus.FOUND,
        LookupStatus.FOUND,
        LookupStatus.AMBIGUOUS,
        LookupStatus.FAILED,
        LookupStatus.NOT_FOUND,
    ]
    assert [item.paper for item in result.items[:2]] == [local, local]
    assert result.items[3].error == "msearch item rejected"
    assert provider.calls == [([arxiv, doi, failed, missing], None)]


@pytest.mark.asyncio
async def test_local_miss_falls_through_to_s2_and_returns_its_complete_paper():
    identity = parse_identifier("2401.00001")
    arxiv = FakeProvider()
    paper = make_paper(
        PaperSource.S2,
        arxiv_id=identity.value,
        title="Semantic Scholar title",
        authors=["First Author", "Second Author"],
        metadata={
            "s2": {
                "paper_id": "a" * 40,
                "corpus_id": "123",
                "citation_count": 4,
                "tldr": {"text": "Contribution"},
            }
        },
    )
    s2 = FakeProvider({identity: PaperLookupMatch([paper])})
    lookup = service(
        {
            PaperSource.ARXIV: binding(arxiv, identifier_kinds=[IdentifierKind.ARXIV]),
            PaperSource.S2: binding(s2, identifier_kinds=[IdentifierKind.ARXIV]),
        }
    )

    result = await lookup.lookup(
        PaperLookup([identity.value])
    )

    assert result.items[0].status == LookupStatus.FOUND
    assert result.items[0].paper.authors == ["First Author et al."]
    assert result.items[0].paper.metadata["s2"]["citation_count"] == 4
    assert arxiv.calls == [([identity], None)]
    assert s2.calls == [([identity], None)]


@pytest.mark.asyncio
async def test_lookup_projects_complete_cached_facts_without_mutating_them():
    identity = parse_identifier("s2:" + "b" * 40)
    paper = make_paper(
        PaperSource.S2,
        arxiv_id="2402.00002",
        title="Complete cached title",
        authors=["Author One", "Author Two"],
        metadata={
            "s2": {
                "citation_count": 8,
                "observed_at": "2026-10-28T00:00:00Z",
                "tldr": {"model": "tldr@v2", "text": "Summary"},
            }
        },
    )
    provider = FakeProvider({identity: PaperLookupMatch([paper])})
    cache = FakeCache()
    lookup = service(
        {
            PaperSource.S2: binding(
                provider,
                cache=cache,
                identifier_kinds=[IdentifierKind.S2],
            )
        }
    )

    query_id = f"s2:{identity.value}"
    enriched = await lookup.lookup(
        PaperLookup([query_id])
    )
    full = await lookup.lookup(PaperLookup([query_id]))

    assert enriched.items[0].status == LookupStatus.FOUND
    assert enriched.items[0].paper.title == "Complete cached title"
    assert enriched.items[0].paper.metadata["s2"]["citation_count"] == 8
    assert full.items[0].paper.authors == ["Author One et al."]
    assert "observed_at" not in full.items[0].paper.metadata["s2"]
    assert full.items[0].paper.metadata["s2"]["tldr"] == "Summary"
    cached = cache.values[identity]
    assert cached.paper.authors == ["Author One", "Author Two"]
    assert cached.paper.metadata["s2"]["observed_at"] == "2026-10-28T00:00:00Z"
    assert cached.paper.metadata["s2"]["tldr"]["model"] == "tldr@v2"
    assert len(provider.calls) == 1
    assert len(cache.puts) == 1


@pytest.mark.asyncio
async def test_lookup_cache_is_shared_across_credentials_without_storing_them():
    identity = parse_identifier("openalex:W123")
    paper = make_paper(
        PaperSource.OPENALEX,
        arxiv_id=None,
        title="First credential result",
        metadata={"openalex": {"id": "https://openalex.org/W123"}},
    )
    provider = FakeProvider({identity: PaperLookupMatch([paper])})
    cache = FakeCache()
    lookup = service(
        {
            PaperSource.OPENALEX: binding(
                provider,
                cache=cache,
                identifier_kinds=[IdentifierKind.OPENALEX],
            )
        }
    )
    query = PaperLookup([f"openalex:{identity.value}"])

    first = await lookup.lookup(query, credentials={PaperSource.OPENALEX: "key-a"})
    second = await lookup.lookup(query, credentials={PaperSource.OPENALEX: "key-b"})
    without_key = await lookup.lookup(query)

    assert first.items[0].paper == second.items[0].paper == without_key.items[0].paper
    assert provider.calls == [([identity], "key-a")]
    assert all(
        "key-a" not in repr(record) and "key-b" not in repr(record)
        for record, _ttl_seconds in cache.puts
    )


@pytest.mark.asyncio
async def test_later_ambiguous_match_does_not_return_a_previously_found_paper():
    identity = parse_identifier("2401.00001")
    base = make_paper()
    local = FakeProvider({identity: PaperLookupMatch([base])})
    ambiguous = FakeProvider(
        {identity: PaperLookupMatch([base, make_paper(title="Other")])}
    )
    lookup = service(
        {
            "arxiv": binding(local, has_signals=lambda metadata: False),
            "s2": binding(ambiguous),
        }
    )

    result = await lookup.lookup(PaperLookup([identity.value]))

    item = result.items[0]
    assert item.status == LookupStatus.AMBIGUOUS
    assert item.paper is None


def test_identifier_kind_values_and_members():
    assert IdentifierKind.values() == ["arxiv", "doi", "openalex", "s2", "corpus_id"]
    assert IdentifierKind.members() == [
        IdentifierKind.ARXIV,
        IdentifierKind.DOI,
        IdentifierKind.OPENALEX,
        IdentifierKind.S2,
        IdentifierKind.CORPUS_ID,
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("publication_date", "reading_url", "extra_arxiv", "extra_s2"),
    [
        (date(2017, 6, 12), "https://example.test/paper.pdf", {}, {}),
        (
            date(2018, 1, 1), "https://publisher.test/accepted.pdf",
            {"submitted_date": "2017-06-12"},
            {"open_access_pdf": "https://publisher.test/accepted.pdf"},
        ),
    ],
)
async def test_transformer_lookup_preserves_research_signals_and_removes_internal_data(
    publication_date, reading_url, extra_arxiv, extra_s2
):
    """使用实际 arXiv/S2 字段，保护研读信号与内部溯源的出口边界。"""
    identity = parse_identifier("1706.03762")
    paper = make_paper(
        arxiv_id=identity.value,
        metadata={
            "internal": {"cache_note": "not a registered metadata source"},
            "arxiv": {
                "raw_id": "1706.03762", "categories": ["cs.CL", "cs.LG"],
                "submitted_date": "2017-06-12", "updated_date": "2023-08-03",
                "versions": [
                    {"version": "v1", "created": "Mon, 12 Jun 2017 17:57:34 GMT"},
                    {"version": "v7", "created": "Wed, 2 Aug 2023 00:41:18 GMT"},
                ],
                "journal_ref": None, "comments": "15 pages, 5 figures",
                "license": "http://arxiv.org/licenses/nonexclusive-distrib/1.0/",
            },
            "s2": {
                "corpus_id": "13756489", "paper_id": "a" * 40,
                "citation_count": 197480, "influential_citation_count": 20998,
                "is_open_access": False,
                "tldr": {"model": "tldr@v2.0.0", "text": "The Transformer uses attention instead of recurrence and convolutions."},
                "open_access_pdf": reading_url,
                "release": "2026-10-20", "observed_at": "2026-10-29",
                "paper_source": {"path": "papers_part_0000.json.gz", "line": 1237438},
                "tldr_source": {"path": "tldrs_part_0026.json.gz", "line": 2200713},
                "bibtex": "Repeated bibliography",
            },
        },
    )
    paper.publication_date = publication_date
    original = deepcopy(paper)
    lookup = service({"arxiv": binding(FakeProvider({identity: PaperLookupMatch([paper])}))})

    result = await lookup.lookup(PaperLookup([identity.value]))

    assert result.items[0].paper.metadata == {
        "arxiv": {
            "categories": ["cs.CL", "cs.LG"], "updated_date": "2023-08-03",
            "comments": "15 pages, 5 figures", "latest_version": "v7",
            **extra_arxiv,
        },
        "s2": {
            "corpus_id": "13756489", "citation_count": 197480,
            "influential_citation_count": 20998,
            "tldr": "The Transformer uses attention instead of recurrence and convolutions.",
            **extra_s2,
        },
    }
    assert paper == original


@pytest.mark.asyncio
async def test_medical_lookup_compacts_mesh_and_preserves_distinct_reading_entries():
    identity = parse_identifier("10.1056/nejmoa2034577")
    mesh = {
        "descriptor_ui": "D000086382", "descriptor_name": "COVID-19",
        "qualifier_ui": "Q000276", "qualifier_name": "immunology",
        "is_major_topic": False,
    }
    paper = make_paper(
        source="openalex", arxiv_id=None, doi=identity.value,
        metadata={"openalex": {
            "id": "https://openalex.org/W3111255098",
            "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/33301246"},
            "updated_date": "2026-10-29", "cited_by_count": 0,
            "venue": "The New England Journal of Medicine", "is_retracted": False,
            "primary_topic": {"id": "T1", "display_name": "SARS-CoV-2 and COVID-19 Research", "score": 1, "domain": {"id": "D1"}},
            "study_designs": [
                {"id": "randomized-controlled-trial", "display_name": "Randomized Controlled Trial"},
                {"id": "clinical-trial", "display_name": "Clinical Trial"},
            ],
            "mesh": [mesh, dict(mesh), {**mesh, "qualifier_name": "prevention & control"}],
            "arxiv_ids": [],
            "best_oa_location": {
                "landing_page_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7745181/",
                "pdf_url": "https://example.test/paper.pdf",
                "license": "cc-by", "version": "acceptedVersion",
            },
            "open_access": {
                "is_oa": True, "oa_status": "green",
                "oa_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7745181/",
                "any_repository_has_fulltext": True,
            },
        }},
    )
    lookup = service({"arxiv": binding(FakeProvider({identity: PaperLookupMatch([paper])}))})

    result = await lookup.lookup(PaperLookup([identity.value]))

    assert result.items[0].paper.metadata == {"openalex": {
        "id": "https://openalex.org/W3111255098", "cited_by_count": 0,
        "venue": "The New England Journal of Medicine", "is_retracted": False,
        "primary_topic": "SARS-CoV-2 and COVID-19 Research",
        "study_designs": ["Randomized Controlled Trial", "Clinical Trial"],
        "mesh": [
            {"descriptor_name": "COVID-19", "qualifier_name": "immunology", "is_major_topic": False},
            {"descriptor_name": "COVID-19", "qualifier_name": "prevention & control", "is_major_topic": False},
        ],
        "best_oa_location": {"landing_page_url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7745181/"},
    }}

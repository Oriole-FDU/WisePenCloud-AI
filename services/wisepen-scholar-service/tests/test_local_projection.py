"""来源返回完整事实，应用出口制作紧凑副本。"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from scholar.application.paper_projection import project_paper
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import parse_identifier
from scholar.domain.models.paper import Paper
from scholar.core.providers import ArxivProvider


@pytest.fixture
def local_paper():
    return Paper(
        arxiv_id="2401.00001",
        doi=None,
        title="A paper",
        abstract="An abstract",
        authors=["First", "Second", "Third"],
        publication_date=None,
        year=2024,
        url="https://arxiv.org/abs/2401.00001",
        pdf_url=None,
        source="arxiv",
        metadata={
            "arxiv": {"license": "MIT"},
            "s2": {
                "tldr": {"model": "old", "text": "Summary"},
                "observed_at": "old",
                "citation_count": 0,
                "is_open_access": False,
            },
        },
    )


@pytest.mark.asyncio
async def test_arxiv_search_preserves_full_facts_after_ranking(local_paper):
    original = deepcopy(local_paper)
    repository = SimpleNamespace(
        search=AsyncMock(return_value=[local_paper]),
        get_many=AsyncMock(return_value=[local_paper]),
    )
    provider = ArxivProvider(
        repository,
        SimpleNamespace(embed=AsyncMock(return_value=[[1.0]])),
        vector_repository=SimpleNamespace(
            search=AsyncMock(return_value=[local_paper.arxiv_id])
        ),
    )
    result = await provider.search(
        query="paper",
        field=AcademicField.GENERAL,
        published_after=None,
        published_before=None,
        limit=10,
    )
    paper = result.papers[0]
    assert paper.doi == "10.48550/arxiv.2401.00001"
    assert local_paper == original


@pytest.mark.asyncio
async def test_arxiv_lookup_preserves_ambiguity_and_item_error(local_paper):
    ambiguous = PaperLookupMatch([local_paper, deepcopy(local_paper)])
    failed = PaperLookupMatch([], "shard unavailable")
    repository = SimpleNamespace(lookup=AsyncMock(return_value=[ambiguous, failed]))
    provider = ArxivProvider(
        repository,
        embedding_client=None,
        vector_repository=SimpleNamespace(search=AsyncMock(return_value=[])),
    )

    results = await provider.lookup(
        [
            parse_identifier("10.48550/arxiv.2401.00001"),
            parse_identifier("2401.00002"),
        ]
    )

    assert repository.lookup.await_args.args[0] == [
        parse_identifier("2401.00001"),
        parse_identifier("2401.00002"),
    ]
    assert len(results[0].papers) == 2
    assert results[0].papers[0].doi == "10.48550/arxiv.2401.00001"
    assert results[0].papers[0].authors == ["First", "Second", "Third"]
    assert results[0].papers[0].metadata["s2"]["observed_at"] == "old"
    assert results[1] == failed


@pytest.mark.parametrize(
    ("authors", "expected"),
    [([], []), (["Only"], ["Only"]), (["First", "Second"], ["First et al."])],
)
def test_project_paper_compacts_copy_without_mutating_source(
    local_paper, authors, expected
):
    local_paper.authors = authors
    original = deepcopy(local_paper)

    projected = project_paper(local_paper)

    assert projected.authors == expected
    assert projected.metadata["s2"]["tldr"] == "Summary"
    assert "observed_at" not in projected.metadata["s2"]
    assert local_paper == original

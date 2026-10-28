"""来源搜索投影紧凑副本，lookup 保留本地完整事实。"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import parse_identifier
from scholar.domain.models.paper import Paper
from scholar.providers.arxiv import ArxivProvider


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
async def test_arxiv_search_returns_compact_copy_after_ranking(local_paper):
    original = deepcopy(local_paper)
    repository = SimpleNamespace(
        search=AsyncMock(return_value=[local_paper]),
        search_vector=AsyncMock(return_value=[local_paper]),
    )
    provider = ArxivProvider(
        repository, SimpleNamespace(embed=AsyncMock(return_value=[[1.0]]))
    )
    result = await provider.search(
        query="paper",
        field=AcademicField.GENERAL,
        published_after=None,
        published_before=None,
        limit=10,
    )
    paper = result.papers[0]
    assert paper.authors == ["First et al."]
    assert paper.metadata["s2"] == {
        "tldr": {"text": "Summary"},
        "citation_count": 0,
        "is_open_access": False,
    }
    assert paper.metadata["arxiv"] == {"license": "MIT"}
    assert local_paper == original


@pytest.mark.asyncio
async def test_arxiv_lookup_preserves_ambiguity_and_item_error(local_paper):
    ambiguous = PaperLookupMatch([local_paper, deepcopy(local_paper)])
    failed = PaperLookupMatch([], "shard unavailable")
    repository = SimpleNamespace(lookup=AsyncMock(return_value=[ambiguous, failed]))
    provider = ArxivProvider(repository, embedding_client=None)

    results = await provider.lookup(
        [parse_identifier("2401.00001"), parse_identifier("2401.00002")]
    )

    assert results == [ambiguous, failed]
    assert results[0].papers[0].authors == ["First", "Second", "Third"]
    assert results[0].papers[0].metadata["s2"]["observed_at"] == "old"
    assert results[1].error == "shard unavailable"


@pytest.mark.parametrize(
    ("authors", "expected"),
    [([], []), (["Only"], ["Only"]), (["First", "Second"], ["First et al."])],
)
def test_project_paper_compacts_copy_without_mutating_source(
    local_paper, authors, expected
):
    local_paper.authors = authors
    original = deepcopy(local_paper)

    projected = local_paper.project()

    assert projected.authors == expected
    assert projected.metadata["s2"]["tldr"] == {"text": "Summary"}
    assert "observed_at" not in projected.metadata["s2"]
    assert local_paper == original

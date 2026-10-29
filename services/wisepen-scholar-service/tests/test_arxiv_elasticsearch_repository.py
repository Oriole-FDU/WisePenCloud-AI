from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scholar.core.storage.elasticsearch.paper_repository import (
    ElasticsearchPaperRepository,
)


def document(arxiv_id):
    return {
        "arxiv_id": arxiv_id,
        "raw_id": arxiv_id,
        "doi": None,
        "title": arxiv_id,
        "abstract": "Abstract",
        "authors": [],
        "categories": ["cs.AI"],
        "submitted_date": "2025-01-01",
        "updated_date": "2025-01-02",
        "versions": [],
        "journal_ref": None,
        "comments": None,
        "license": None,
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": None,
    }


@pytest.mark.asyncio
async def test_get_many_restores_requested_order_after_batch_lookup():
    es = SimpleNamespace(
        mget=AsyncMock(
            return_value={
                "docs": [
                    {"_id": "2501.00002", "_source": document("2501.00002"), "found": True},
                    {"_id": "2501.00001", "_source": document("2501.00001"), "found": True},
                ]
            }
        )
    )
    repository = ElasticsearchPaperRepository(es, "papers")

    papers = await repository.get_many(["2501.00001", "2501.00002"])

    assert [paper.arxiv_id for paper in papers] == ["2501.00001", "2501.00002"]

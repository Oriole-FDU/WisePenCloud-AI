from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from qdrant_client.http.models import Distance, VectorParams

from scholar.core.storage.qdrant.paper_vectors import (
    QdrantPaperVectorRepository,
    ensure_collection,
    point_id,
    point_payload,
)
from scholar.ml.specter import DIMENSIONS


def test_point_payload_contains_only_vector_filter_facts():
    payload = point_payload(
        {
            "arxiv_id": "2501.00001",
            "categories": ["cs.AI", "math.PR", "cs.CL"],
            "submitted_date": "2025-01-02",
            "title": "Not copied to Qdrant",
        }
    )

    assert payload == {
        "arxiv_id": "2501.00001",
        "categories": ["cs.AI", "math.PR", "cs.CL"],
        "category_groups": ["cs", "math"],
        "submitted_date": "2025-01-02T00:00:00Z",
    }
    assert UUID(point_id("2501.00001")).version == 5


@pytest.mark.asyncio
async def test_dense_search_ors_categories_and_ands_inclusive_date_bounds():
    client = SimpleNamespace(
        query_points=AsyncMock(
            return_value=SimpleNamespace(
                points=[
                    SimpleNamespace(payload={"arxiv_id": "2501.00002"}),
                    SimpleNamespace(payload={"arxiv_id": "2501.00001"}),
                ]
            )
        )
    )
    repository = QdrantPaperVectorRepository(client, "test-collection")

    ids = await repository.search(
        vector=[0.25] * DIMENSIONS,
        categories=["cs.*", "math.PR"],
        published_after=date(2025, 1, 1),
        published_before=date(2025, 1, 31),
        limit=25,
        num_candidates=300,
    )

    request = client.query_points.await_args.kwargs
    assert ids == ["2501.00002", "2501.00001"]
    assert request["query_filter"].should[0].key == "category_groups"
    assert request["query_filter"].should[0].match.value == "cs"
    assert request["query_filter"].should[1].key == "categories"
    assert request["query_filter"].should[1].match.value == "math.PR"
    assert request["query_filter"].must[0].key == "submitted_date"
    assert request["query_filter"].must[0].range.gte == datetime(
        2025, 1, 1, tzinfo=UTC
    )
    assert request["query_filter"].must[0].range.lte == datetime(
        2025, 1, 31, tzinfo=UTC
    )
    assert request["search_params"].hnsw_ef == 300
    assert request["with_payload"] == ["arxiv_id"]
    assert request["with_vectors"] is False


@pytest.mark.asyncio
async def test_ensure_collection_creates_euclid_vectors_and_payload_indexes():
    client = SimpleNamespace(
        collection_exists=AsyncMock(return_value=False),
        create_collection=AsyncMock(),
        create_payload_index=AsyncMock(),
    )

    await ensure_collection(client, "test-collection")

    config = client.create_collection.await_args.kwargs
    assert config["vectors_config"] == VectorParams(
        size=DIMENSIONS,
        distance=Distance.EUCLID,
        on_disk=True,
    )
    assert config["hnsw_config"].on_disk is True
    assert client.create_payload_index.await_count == 3


@pytest.mark.asyncio
async def test_ensure_collection_rejects_existing_incompatible_vectors():
    client = SimpleNamespace(
        collection_exists=AsyncMock(return_value=True),
        get_collection=AsyncMock(
            return_value=SimpleNamespace(
                config=SimpleNamespace(
                    params=SimpleNamespace(
                        vectors=VectorParams(size=DIMENSIONS, distance=Distance.COSINE)
                    )
                )
            )
        ),
        create_payload_index=AsyncMock(),
    )

    with pytest.raises(ValueError, match="must use a 768-dimensional Euclid vector"):
        await ensure_collection(client, "test-collection")

    client.create_payload_index.assert_not_awaited()

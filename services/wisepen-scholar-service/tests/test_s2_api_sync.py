"""每日同步的真实 HTTP 契约及向量持久化；不启动本地编码器。"""

from unittest.mock import AsyncMock

import pytest
from offline.arxiv import open_database
from offline.arxiv_sync import sync_arxiv
from offline.embeddings import publication_documents
from offline.s2_api import S2SyncClient
from scholar.domain.models.identifiers import parse_identifier
from test_arxiv_sync import FakeCache, FakeHarvester, active_record, harvest, publisher


@pytest.fixture
def db(tmp_path):
    with open_database(tmp_path / "daily.duckdb") as database:
        yield database


@pytest.mark.asyncio
async def test_daily_sync_requests_v2_and_persists_vector_for_publication(db):
    requests = []

    async def request(method, path, **kwargs):
        requests.append((method, path, kwargs))
        return [
            {
                "paperId": "a" * 40,
                "externalIds": {"ArXiv": "2501.12948"},
                "citationCount": 0,
                "influentialCitationCount": 0,
                "isOpenAccess": False,
                "tldr": {"text": "summary", "model": "tldr"},
                "embedding": {"model": "specter_v2", "vector": [0.25] * 768},
            }
        ]

    client = S2SyncClient(AsyncMock(request=request))
    harvester = FakeHarvester(
        harvest("2026-10-28T00:00:00Z", active_record("2501.12948", "2026-10-28"))
    )
    publish, _ = publisher(db)
    result = await sync_arxiv(db, harvester, client, publish=publish, cache=FakeCache())
    assert "embedding.specter_v2" in requests[0][2]["params"]["fields"].split(",")
    assert "embedding" not in requests[0][2]["params"]["fields"].split(",")
    assert result["pending_s2"] == 0
    rows = db.execute("SELECT arxiv_id,document FROM arxiv").fetchall()
    document = publication_documents(db, rows, vector_enabled=True)[0]
    assert document["embedding"] == [0.25] * 768
    assert document["metadata"]["s2"]["citation_count"] == 0
    assert document["metadata"]["s2"]["is_open_access"] is False
    assert "embedding" not in document["metadata"]["s2"]
    assert db.execute("SELECT count(*) FROM pending_embeddings").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_missing_s2_vector_keeps_daily_retry_without_gpu(db):
    request = AsyncMock(
        return_value=[
            {
                "paperId": "a" * 40,
                "externalIds": {"ArXiv": "2501.12948"},
                "embedding": None,
            }
        ]
    )
    publish, _ = publisher(db)
    result = await sync_arxiv(
        db,
        FakeHarvester(
            harvest("2026-10-28T00:00:00Z", active_record("2501.12948", "2026-10-28"))
        ),
        S2SyncClient(AsyncMock(request=request)),
        publish=publish,
        cache=FakeCache(),
    )
    assert result["pending_s2"] == 1
    assert db.execute("SELECT count(*) FROM pending_embeddings").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_v1_response_is_rejected_instead_of_mixing_vector_spaces():
    client = S2SyncClient(
        AsyncMock(
            request=AsyncMock(
                return_value=[
                    {"embedding": {"model": "specter_v1", "vector": [1.0] * 768}}
                ]
            )
        )
    )
    with pytest.raises(ValueError):
        await client.enrich([parse_identifier("2501.12948")])

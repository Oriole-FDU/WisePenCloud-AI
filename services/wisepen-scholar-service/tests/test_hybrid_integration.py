"""需要显式启用真实 ES 的小规模 ES、Qdrant 与 DuckDB 发布验收。"""

from __future__ import annotations

import os
from datetime import date

import orjson
import pytest
from qdrant_client import AsyncQdrantClient

from offline.arxiv import open_database
from offline.publish import publish_arxiv
from scholar.core.providers import ArxivProvider
from scholar.core.storage.elasticsearch.paper_repository import (
    ElasticsearchPaperRepository,
    create_elasticsearch,
)
from scholar.core.storage.qdrant.paper_vectors import (
    COLLECTION,
    QdrantPaperVectorRepository,
    point_id,
)
from scholar.domain.models.fields import AcademicField
from scholar.ml.specter import DIMENSIONS, MODEL_SIGNATURE

pytestmark = pytest.mark.skipif(
    os.getenv("SCHOLAR_REAL_ES") != "1",
    reason="Set SCHOLAR_REAL_ES=1 to run the real ES and in-memory Qdrant acceptance",
)


def paper(arxiv_id, categories, submitted_date):
    return {
        "arxiv_id": arxiv_id,
        "raw_id": arxiv_id,
        "doi": None,
        "title": f"arXiv fixture {arxiv_id}",
        "abstract": "A controlled retrieval fixture.",
        "authors": ["Test Author"],
        "categories": categories,
        "submitted_date": submitted_date,
        "updated_date": submitted_date,
        "versions": [{"version": "v1", "created": f"{submitted_date}T00:00:00Z"}],
        "journal_ref": None,
        "comments": None,
        "license": None,
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": None,
        "metadata": {"s2": {"citation_count": 0}},
    }


class FixedEmbedding:
    def __init__(self, vector):
        self.vector = vector

    async def embed(self, *, texts):
        return [self.vector]


@pytest.mark.asyncio
async def test_real_es_qdrant_duckdb_publish_and_online_hybrid_search(tmp_path):
    from scholar.core.config.app_settings import settings

    alias = f"scholar-hybrid-check-{os.urandom(8).hex()}"
    qdrant = AsyncQdrantClient(location=":memory:")
    query_vector = [0.0] * DIMENSIONS
    query_vector[0] = 1.0
    semantic_best = [0.0] * DIMENSIONS
    semantic_best[1] = 1.0
    papers = [
        paper("2501.00001", ["cs.AI"], "2025-01-01"),
        paper("2501.00002", ["math.PR"], "2025-01-03"),
        paper("2501.00003", ["cs.CL"], "2025-01-04"),
        paper("2501.00004", ["q-bio.BM"], "2024-12-31"),
    ]
    vectors = {
        item["arxiv_id"]: semantic_best
        if item["arxiv_id"] == "2501.00004"
        else query_vector
        for item in papers
    }

    try:
        async with create_elasticsearch(
            settings.ELASTICSEARCH_URIS,
            settings.ELASTICSEARCH_USERNAME,
            settings.ELASTICSEARCH_PASSWORD,
            120,
        ) as es:
            try:
                with open_database(tmp_path / "papers.duckdb") as db:
                    db.execute(
                        "INSERT INTO import_state(singleton,complete,published) VALUES(true,true,false)"
                    )
                    for document in papers:
                        arxiv_id = document["arxiv_id"]
                        encoded = orjson.dumps(document).decode()
                        db.execute(
                            "INSERT INTO arxiv VALUES(?, ?, '{}'::JSON, ?::JSON)",
                            [arxiv_id, document["doi"], encoded],
                        )
                        fingerprint = db.execute(
                            "SELECT sha256((document->>'title') || chr(0) || (document->>'abstract')) FROM arxiv WHERE arxiv_id=?",
                            [arxiv_id],
                        ).fetchone()[0]
                        db.execute(
                            "INSERT INTO paper_embeddings VALUES(?, ?, ?, ?)",
                            [arxiv_id, MODEL_SIGNATURE, fingerprint, vectors[arxiv_id]],
                        )
                        db.execute(
                            "INSERT INTO pending_publish VALUES(?)", [arxiv_id]
                        )

                    status = await publish_arxiv(
                        db, es, alias, qdrant=qdrant, batch_size=2
                    )
                    assert status["pending"] == 0
                    assert status["pending_delete"] == 0
                    assert (await qdrant.count(COLLECTION, exact=True)).count == 4

                    mapping = await es.indices.get_mapping(index=alias)
                    properties = next(iter(mapping.values()))["mappings"]["properties"]
                    stored = await es.get(index=alias, id="2501.00001")
                    assert "embedding" not in properties
                    assert "embedding" not in stored["_source"]

                    vector_repository = QdrantPaperVectorRepository(qdrant)
                    bounded = await vector_repository.search(
                        vector=query_vector,
                        categories=["cs.*", "math.PR"],
                        published_after=date(2025, 1, 1),
                        published_before=date(2025, 1, 3),
                        limit=2,
                        num_candidates=10,
                    )
                    assert set(bounded) == {"2501.00001", "2501.00002"}

                    after = await vector_repository.search(
                        vector=query_vector,
                        categories=[],
                        published_after=date(2025, 1, 3),
                        published_before=None,
                        limit=10,
                        num_candidates=10,
                    )
                    assert set(after) == {"2501.00002", "2501.00003"}
                    before = await vector_repository.search(
                        vector=query_vector,
                        categories=[],
                        published_after=None,
                        published_before=date(2025, 1, 1),
                        limit=10,
                        num_candidates=10,
                    )
                    assert set(before) == {"2501.00001", "2501.00004"}

                    qdrant_order = await vector_repository.search(
                        vector=semantic_best,
                        categories=[],
                        published_after=None,
                        published_before=None,
                        limit=4,
                        num_candidates=10,
                    )
                    provider = ArxivProvider(
                        ElasticsearchPaperRepository(es, alias),
                        FixedEmbedding(semantic_best),
                        vector_repository=vector_repository,
                        candidate_limit=4,
                        num_candidates=10,
                    )
                    result = await provider.search(
                        query="zqnvqwhxnoindexhit938271",
                        field=AcademicField.GENERAL,
                        published_after=None,
                        published_before=None,
                        limit=4,
                    )
                    assert result.partial_error is None
                    assert [item.arxiv_id for item in result.papers] == qdrant_order
                    assert result.papers[0].arxiv_id == "2501.00004"

                    # 元数据变更不改变标题/摘要指纹，发布复用已有 DuckDB 向量。
                    unchanged = db.execute(
                        "SELECT document FROM arxiv WHERE arxiv_id='2501.00001'"
                    ).fetchone()[0]
                    updated = orjson.loads(unchanged)
                    updated["metadata"]["s2"]["citation_count"] = 7
                    db.execute(
                        "UPDATE arxiv SET document=?::JSON WHERE arxiv_id='2501.00001'",
                        [orjson.dumps(updated).decode()],
                    )
                    db.execute(
                        "INSERT INTO pending_publish VALUES('2501.00001')"
                    )
                    status = await publish_arxiv(db, es, alias, qdrant=qdrant)
                    assert status["pending"] == 0
                    assert (await qdrant.count(COLLECTION, exact=True)).count == 4

                    # 标题变化使旧向量失效，旧 Qdrant 点被移除，ES 仍发布论文全文。
                    stale = db.execute(
                        "SELECT document FROM arxiv WHERE arxiv_id='2501.00002'"
                    ).fetchone()[0]
                    changed = orjson.loads(stale)
                    changed["title"] = "Changed retrieval input"
                    db.execute(
                        "UPDATE arxiv SET document=?::JSON WHERE arxiv_id='2501.00002'",
                        [orjson.dumps(changed).decode()],
                    )
                    db.execute(
                        "INSERT INTO pending_publish VALUES('2501.00002')"
                    )
                    status = await publish_arxiv(db, es, alias, qdrant=qdrant)
                    assert status["pending"] == 0
                    assert (await qdrant.count(COLLECTION, exact=True)).count == 3
                    assert await qdrant.retrieve(
                        COLLECTION, ids=[point_id("2501.00002")]
                    ) == []
                    assert (
                        await es.get(index=alias, id="2501.00002")
                    )["_source"]["title"] == "Changed retrieval input"

                    db.execute("DELETE FROM arxiv WHERE arxiv_id='2501.00003'")
                    db.execute(
                        "INSERT INTO pending_delete VALUES('2501.00003', '2025-01-05')"
                    )
                    status = await publish_arxiv(db, es, alias, qdrant=qdrant)
                    assert status["pending_delete"] == 0
                    assert (await qdrant.count(COLLECTION, exact=True)).count == 2
                    deleted = await es.mget(index=alias, ids=["2501.00003"])
                    assert deleted["docs"][0]["found"] is False
            finally:
                temporary_indices = await es.indices.get(
                    index=f"{alias}-*", allow_no_indices=True
                )
                for index in temporary_indices:
                    await es.indices.delete(index=index)
    finally:
        await qdrant.close()

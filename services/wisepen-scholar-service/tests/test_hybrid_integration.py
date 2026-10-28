"""显式启用的真实边界验收；只使用临时索引、别名和数据库。"""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import date
from pathlib import Path
from uuid import uuid4

import orjson
import pytest
from common.utils.ai_clients.embedding import EmbeddingClient
from offline.arxiv import open_database
from offline.embeddings import document_worker, generate_embeddings
from offline.publish import publish_arxiv
from openai import AsyncOpenAI
from redis.asyncio import Redis
from scholar.core.storage.elasticsearch.paper_repository import (
    ElasticsearchPaperRepository,
    create_elasticsearch,
)
from scholar.domain.models.fields import AcademicField
from scholar.ml.specter import DEFAULT_MODELS, DIMENSIONS, QUERY_MODEL
from scholar.core.providers import ArxivProvider

pytestmark = pytest.mark.skipif(
    os.getenv("SCHOLAR_REAL_ES") != "1",
    reason="Set SCHOLAR_REAL_ES=1 for real ES/GPU acceptance",
)


@pytest.mark.asyncio
async def test_real_gpu_hybrid_filters_publication_migration_and_rollback(tmp_path):
    from scholar.core.config.app_settings import settings
    from scholar.core.config.bootstrap_settings import SERVICE_ROOT, bootstrap_settings

    spec = importlib.util.spec_from_file_location(
        "scholar_hybrid_migration", SERVICE_ROOT / "deploy/migrate_search_index.py"
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    alias = f"scholar-hybrid-check-{uuid4().hex}"
    indices = []
    async with (
        create_elasticsearch(
            settings.ELASTICSEARCH_URIS,
            settings.ELASTICSEARCH_USERNAME,
            settings.ELASTICSEARCH_PASSWORD,
            120,
        ) as es,
        Redis.from_url(settings.REDIS_URL) as redis,
        AsyncOpenAI(
            api_key="local",
            base_url=f"http://127.0.0.1:{bootstrap_settings.SERVICE_PORT}/v1",
            default_headers={"X-From-Source": settings.FROM_SOURCE_SECRET},
            timeout=2,
            max_retries=0,
        ) as api,
    ):
        client = EmbeddingClient(api, model=QUERY_MODEL, dimensions=DIMENSIONS)
        documents = {}
        for query in ("DeepSeek-R1", "Attention Is All You Need", "language models"):
            result = await es.search(
                index=settings.SCHOLAR_INDEX_ALIAS,
                size=20,
                source_excludes=["embedding"],
                query={
                    "multi_match": {"query": query, "fields": ["title^3", "abstract"]}
                },
            )
            documents.update(
                (hit["_id"], hit["_source"]) for hit in result["hits"]["hits"]
            )
        originals = await es.mget(
            index=settings.SCHOLAR_INDEX_ALIAS,
            ids=["2501.12948", "1706.03762"],
            source_excludes=["embedding"],
        )
        assert all(item["found"] for item in originals["docs"])
        documents.update((item["_id"], item["_source"]) for item in originals["docs"])
        # 同文向量的候选落在不同日期，用真实 kNN 验证预过滤及闭区间边界。
        for identifier, day in (
            ("2501.99998", "2025-01-01"),
            ("2501.99999", "2025-01-02"),
        ):
            documents[identifier] = {
                **documents["2501.12948"],
                "arxiv_id": identifier,
                "categories": ["q-bio.BM"],
                "submitted_date": day,
            }
        try:
            with open_database(tmp_path / "papers.duckdb") as db:
                db.execute(
                    "INSERT INTO import_state(singleton,complete,published) VALUES(true,true,false)"
                )
                for identifier, document in documents.items():
                    db.execute(
                        "INSERT INTO arxiv VALUES(?, ?, '{}'::JSON, ?::JSON)",
                        [identifier, document["doi"], orjson.dumps(document).decode()],
                    )
                    db.execute("INSERT INTO pending_publish VALUES(?)", [identifier])
                async with document_worker(
                    Path(settings.SCHOLAR_EMBEDDING_GPU_PYTHON),
                    Path(settings.SCHOLAR_EMBEDDING_MODELS_PATH or DEFAULT_MODELS),
                ) as worker:
                    await generate_embeddings(db, worker)
                    assert (await generate_embeddings(db, worker))["written"] == 0
                await publish_arxiv(db, es, alias)
                old = db.execute("SELECT target_index FROM import_state").fetchone()[0]
                indices.append(old)
                repository = ElasticsearchPaperRepository(es, alias)
                provider = ArxivProvider(repository, client)
                rankings = {}
                for query, expected, cutoff in (
                    ("DeepSeek-R1", "2501.12948", 5),
                    ("Attention Is All You Need", "1706.03762", 1),
                ):
                    result = await provider.search(
                        query=query,
                        field=AcademicField.COMPUTER_SCIENCE,
                        published_after=None,
                        published_before=None,
                        limit=10,
                    )
                    rankings[query] = [p.arxiv_id for p in result.papers]
                    assert result.partial_error is None
                    assert expected in rankings[query][:cutoff], rankings
                query_vector = (await client.embed(texts=["DeepSeek-R1"]))[0]
                for search in (repository.search, repository.search_vector):
                    results = await search(
                        **(
                            {"query": "DeepSeek-R1"}
                            if search == repository.search
                            else {"vector": query_vector}
                        ),
                        categories=["q-bio.*"],
                        published_after=date(2025, 1, 1),
                        published_before=date(2025, 1, 1),
                        limit=10,
                    )
                    assert [paper.arxiv_id for paper in results] == ["2501.99998"]
                    assert all("embedding" not in paper.metadata for paper in results)
                # 模拟引用更新后的全文发布；ES 向量仍存在。
                db.execute("INSERT INTO pending_publish VALUES('2501.12948')")
                await publish_arxiv(db, es, alias)
                assert (
                    "embedding"
                    in (await es.get(index=alias, id="2501.12948"))["_source"]
                )
                journal = tmp_path / "migration.json"
                state = await migration.migrate(
                    db,
                    es,
                    redis,
                    alias,
                    journal,
                    poll_seconds=0.1,
                    gpu_python=Path(settings.SCHOLAR_EMBEDDING_GPU_PYTHON),
                    models=Path(
                        settings.SCHOLAR_EMBEDDING_MODELS_PATH or DEFAULT_MODELS
                    ),
                    embedding_client=client,
                )
                indices.append(state["new_index"])
                assert set(await es.indices.get_alias(name=alias)) == {
                    state["new_index"]
                }
                await migration.rollback(db, es, state)
                assert set(await es.indices.get_alias(name=alias)) == {old}
                assert (
                    db.execute("SELECT target_index FROM import_state").fetchone()[0]
                    == old
                )
                evidence = {
                    "documents": len(documents),
                    "rankings": rankings,
                    "vector_filters": True,
                    "incremental_preserves_vector": True,
                    "migration_rollback": True,
                }
                (SERVICE_ROOT / ".runtime/hybrid-live-acceptance.json").write_text(
                    json.dumps(evidence, indent=2), encoding="utf8"
                )
                print(json.dumps(evidence))
        finally:
            # 临时别名独占此前缀；失败时也清理创建后尚未写入 journal 的索引。
            temporary_indices = await es.indices.get(
                index=f"{alias}-*", allow_no_indices=True
            )
            for index in temporary_indices:
                await es.indices.delete(index=index)

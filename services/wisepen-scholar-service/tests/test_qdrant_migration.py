"""迁移断点和最终验收必须以 Qdrant 已应用写入为准。"""

import json

import duckdb
import httpx
import pytest
from qdrant_client import AsyncQdrantClient, models

from migrate_qdrant import migrate
from scholar.core.storage.qdrant.paper_vectors import COLLECTION
from scholar.ml.specter import DIMENSIONS


@pytest.mark.asyncio
async def test_migration_waits_for_applied_writes_before_counting(tmp_path):
    client = AsyncQdrantClient(location=":memory:")

    async def upsert(request):
        # 模拟外部协议：不等待时只确认接收，实际点数尚未增加。
        if request.url.params.get("wait") == "true":
            await client.upsert(
                COLLECTION,
                models.Batch(**json.loads(request.content)["batch"]),
                wait=True,
            )
            status = "completed"
        else:
            status = "acknowledged"
        return httpx.Response(200, json={"result": {"status": status}})

    journal = tmp_path / "migration.json"
    try:
        with duckdb.connect(config={"threads": "1"}) as db:
            db.execute("CREATE TABLE arxiv(arxiv_id VARCHAR, document JSON)")
            db.execute(
                f"CREATE TABLE paper_embeddings(arxiv_id VARCHAR, vector FLOAT[{DIMENSIONS}])"
            )
            for identifier in ("2501.00001", "2501.00002"):
                db.execute(
                    "INSERT INTO arxiv VALUES (?, ?)",
                    [identifier, json.dumps({"categories": ["cs.AI"], "submitted_date": "2025-01-01"})],
                )
                db.execute(
                    "INSERT INTO paper_embeddings VALUES (?, ?)",
                    [identifier, [0.25] * DIMENSIONS],
                )
            async with httpx.AsyncClient(
                base_url="http://qdrant.test", transport=httpx.MockTransport(upsert)
            ) as http:
                await migrate(db, client, http, journal, batch_size=1, resume=False)

        state = json.loads(journal.read_text(encoding="utf8"))
        assert state["stage"] == "indexing"
        assert state["written"] == state["count"] == state["total"] == 2
        points, _ = await client.scroll(COLLECTION, with_vectors=False)
        assert {point.payload["arxiv_id"] for point in points} == {
            "2501.00001", "2501.00002"
        }
    finally:
        await client.close()

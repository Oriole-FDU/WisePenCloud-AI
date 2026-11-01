"""已发布基线的增量写入跟随线上别名，不能使用迁移前目标或创建新集合。"""

import json

import httpx
import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse

from offline.arxiv import open_database
from offline.publish import publish_arxiv
from test_arxiv_sync import seed_paper


@pytest.mark.asyncio
@pytest.mark.parametrize("write_fails", [False, True])
async def test_incremental_publish_resolves_existing_alias_and_keeps_failed_pending(tmp_path, write_fails):
    requests = []
    target = "existing-indexed-hybrid"

    async def handle(request):
        requests.append(request)
        if request.url.path == "/aliases":
            return httpx.Response(200, json={"result": {"aliases": [{"alias_name": "papers", "collection_name": target}]}, "status": "ok", "time": 0})
        if request.url.path == f"/collections/{target}/exists":
            return httpx.Response(200, json={"result": {"exists": True}, "status": "ok", "time": 0})
        assert request.method == "PUT" and request.url.path == f"/collections/{target}/points"
        assert request.url.params["wait"] == "true"
        assert json.loads(request.content)["points"][0]["payload"]["arxiv_id"] == "2501.00001"
        if write_fails:
            return httpx.Response(503, json={"status": {"error": "unavailable"}, "time": 0})
        return httpx.Response(200, json={"result": {"operation_id": 1, "status": "completed"}, "status": "ok", "time": 0})

    with open_database(tmp_path / "publish.duckdb") as db:
        seed_paper(db, "2501.00001", None)
        db.execute("INSERT INTO import_state(singleton,complete,published,target_collection) VALUES (true,true,true,'old-es-index')")
        db.execute("INSERT INTO pending_publish VALUES ('2501.00001')")
        client = AsyncQdrantClient(url="http://qdrant.test", transport=httpx.MockTransport(handle), cloud_inference=True, check_compatibility=False)
        try:
            if write_fails:
                with pytest.raises(UnexpectedResponse) as caught:
                    await publish_arxiv(db, "papers", qdrant=client)
                assert caught.value.status_code == 503
            else:
                await publish_arxiv(db, "papers", qdrant=client)
            assert db.execute("SELECT target_collection FROM import_state").fetchone() == (target,)
            assert db.execute("SELECT count(*) FROM pending_publish").fetchone()[0] == int(write_fails)
        finally:
            await client.close()
    assert len(requests) == 3

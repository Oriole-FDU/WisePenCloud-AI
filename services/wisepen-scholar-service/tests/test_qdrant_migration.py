"""并发灌库以已应用批次推进连续断点，失败重放保持幂等。"""

import asyncio
import hashlib
import gzip
import json
from types import SimpleNamespace

import duckdb
import httpx
import pytest

from migrate_qdrant import migrate
from offline.publish import COLLECTION
from scholar.ml.specter import DIMENSIONS, MODEL_SIGNATURE


@pytest.fixture
def db():
    db = duckdb.connect(config={"threads":"1"})
    db.execute("CREATE TABLE arxiv(arxiv_id VARCHAR, document JSON)")
    db.execute(f"CREATE TABLE paper_embeddings(arxiv_id VARCHAR,model_signature VARCHAR,input_fingerprint VARCHAR,vector FLOAT[{DIMENSIONS}])")
    for index in range(2):
        document={"arxiv_id":f"2501.0000{index+1}","title":f"Title {index}","abstract":"Abstract", "categories":["cs.AI"],"submitted_date":"2025-01-01","metadata":{}}
        db.execute("INSERT INTO arxiv VALUES (?,?)",[document["arxiv_id"],json.dumps(document)])
        if index==0:
            fingerprint=hashlib.sha256((document["title"]+chr(0)+document["abstract"]).encode()).hexdigest()
            db.execute("INSERT INTO paper_embeddings VALUES (?,?,?,?)",[document["arxiv_id"],MODEL_SIGNATURE,fingerprint,[0.25]*DIMENSIONS])
    yield db
    db.close()


class QdrantBoundary:
    def __init__(self):
        self.points={}

    async def collection_exists(self, collection):
        return True

    async def count(self, collection, *, exact):
        return SimpleNamespace(count=len(self.points))

    async def update_collection(self, *args, **kwargs):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_first",[False,True])
async def test_concurrent_upload_preserves_contiguous_checkpoint_and_can_resume(db,tmp_path,fail_first):
    client=QdrantBoundary()
    journal=tmp_path/"migration.json"
    second_applied=asyncio.Event()
    seen=[]

    async def upsert(request):
        assert request.url.params["wait"]=="true"
        assert request.headers["Content-Encoding"]=="gzip"
        point=json.loads(gzip.decompress(request.content))["points"][0]
        seen.append(point)
        if point["payload"]["arxiv_id"]=="2501.00001":
            await asyncio.wait_for(second_applied.wait(),timeout=2)
            if fail_first:
                return httpx.Response(503,json={"error":"temporary failure"})
        else:
            # 后批完成时，队首尚未成功，断点必须停在零；它不能跨过前批。
            assert json.loads(journal.read_text())["written"]==0
            second_applied.set()
        client.points[point["id"]]=point
        return httpx.Response(200,json={"result":{"status":"completed"}})

    async with httpx.AsyncClient(base_url="http://qdrant.test",transport=httpx.MockTransport(upsert)) as http:
        if fail_first:
            with pytest.raises(httpx.HTTPStatusError):
                await migrate(db,client,http,journal,batch_size=1,resume=False,concurrency=2)
            assert json.loads(journal.read_text())["written"]==0
        else:
            await migrate(db,client,http,journal,batch_size=1,resume=False,concurrency=2)

    if fail_first:
        async def replay(request):
            for point in json.loads(gzip.decompress(request.content))["points"]:
                client.points[point["id"]]=point
            return httpx.Response(200,json={"result":{"status":"completed"}})
        async with httpx.AsyncClient(base_url="http://qdrant.test",transport=httpx.MockTransport(replay)) as http:
            await migrate(db,client,http,journal,batch_size=2,resume=True,concurrency=4)
    state=json.loads(journal.read_text())
    assert state["stage"]=="indexing"
    assert state["written"]==state["count"]==state["total"]==2
    assert state["dense_written"]==1
    points={point["payload"]["arxiv_id"]:point for point in client.points.values()}
    assert points["2501.00001"]["vector"]["dense"]==[0.25]*DIMENSIONS
    assert "dense" not in points["2501.00002"]["vector"]
    assert points["2501.00002"]["vector"]["bm25_title"]["text"]=="Title 1"
    assert all(point["vector"]["bm25_abstract"]["model"]=="Qdrant/bm25" for point in points.values())


@pytest.mark.asyncio
async def test_accepted_but_unapplied_write_does_not_advance_checkpoint(db,tmp_path):
    client=QdrantBoundary()
    journal=tmp_path/"migration.json"
    async with httpx.AsyncClient(base_url="http://qdrant.test",transport=httpx.MockTransport(lambda request:httpx.Response(200,json={"result":{"status":"acknowledged"}}))) as http:
        with pytest.raises(RuntimeError,match="not applied"):
            await migrate(db,client,http,journal,batch_size=2,resume=False,concurrency=1)
    assert json.loads(journal.read_text())["written"]==0

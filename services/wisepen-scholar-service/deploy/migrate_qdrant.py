"""将 DuckDB 论文和已有向量迁入独立 Qdrant 混合集合，由现有看板监督和续跑。"""

import argparse
import asyncio
import gzip
import json
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

import duckdb
import httpx
import numpy as np
import orjson
from qdrant_client import AsyncQdrantClient, models
from offline.arxiv import open_database
from scholar.core.config.bootstrap_settings import SERVICE_ROOT
from offline.publish import (
    COLLECTION, ensure_collection, point_id, point_payload, point_vectors, switch_alias,
)
from scholar.core.storage.qdrant.paper_repository import (
    BM25_OPTIONS, QdrantPaperRepository,
)
from offline.embeddings import publication_documents, publication_vectors
from scholar.ml.specter import MODEL_SIGNATURE


def record(path, state, **changes):
    state.update(changes, updated_at=datetime.now(UTC).isoformat())
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    print(json.dumps(state, ensure_ascii=False), flush=True)


async def migrate(db, client, http, journal, *, batch_size, resume, concurrency=4, collection=COLLECTION):
    state = json.loads(journal.read_text(encoding="utf-8")) if resume else {
        "collection":collection, "model_signature":MODEL_SIGNATURE, "bm25_options":BM25_OPTIONS,
        "accepted":0, "dense_accepted":0, "source_rowid":-1, "stage":"upload",
        "total":db.execute("SELECT count(*) FROM arxiv").fetchone()[0],
    }
    # 旧断点记录的是已应用数量，也满足新的已接收前缀合同，可直接续跑。
    if "written" in state:
        state["accepted"] = state.pop("written")
        state["dense_accepted"] = state.pop("dense_written")
    if state["collection"] != collection or state["model_signature"] != MODEL_SIGNATURE or state["bm25_options"] != BM25_OPTIONS:
        raise ValueError("migration journal belongs to a different collection or encoding contract")
    if state["stage"] != "upload":
        return state
    await ensure_collection(client, collection, bulk=True)
    record(journal, state)
    source = db.cursor()
    # 单表扫描保留 DuckDB 插入顺序；避免对数百万篇 JSON 与向量做全表 join/sort。
    source.execute("SELECT rowid,arxiv_id,document FROM arxiv WHERE rowid>?", [state["source_rowid"]])

    def read_batch():
        started = monotonic()
        rows = source.fetchmany(batch_size)
        if not rows:
            return None
        records = [(identifier, raw) for _, identifier, raw in rows]
        documents = publication_documents(records)
        # 已解析的论文直接用于指纹校验；float32 由 orjson 编码，省去逐点 SDK 校验和 Python float 展开。
        vectors = publication_vectors(db, [(identifier, document) for (identifier, _), document in zip(records, documents, strict=True)])
        points = []
        for (identifier, _), document in zip(records, documents, strict=True):
            values = {name: value.model_dump(mode="json") for name, value in point_vectors(document).items()}
            if identifier in vectors:
                values["dense"] = np.asarray(vectors[identifier], dtype=np.float32)
            points.append({"id": point_id(identifier), "vector": values, "payload": point_payload(document)})
        body = orjson.dumps({"points": points}, option=orjson.OPT_SERIALIZE_NUMPY)
        # 现有 Qdrant 已验证接受 gzip 请求；level=1 降低传输量，避免压缩占满组装线程。
        compressed = gzip.compress(body, compresslevel=1)
        return rows[-1][0], len(rows), len(vectors), compressed, monotonic() - started, len(body)

    async def upload(body):
        started = monotonic()
        response = await http.put(
            f"/collections/{collection}/points", params={"wait": "false"}, content=body,
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )
        response.raise_for_status()
        if response.json()["result"]["status"] not in ("acknowledged", "completed"):
            raise RuntimeError(f"Qdrant upload was not accepted: {response.text}")
        return monotonic() - started

    pending = []
    async def acknowledge():
        task, rowid, count, dense, prepare_seconds, size, raw_size = pending[0]
        upload_seconds = await task
        pending.pop(0)
        # 仅在 WAL 接收确认后推进连续提交断点；已接收不代表已经应用或完成索引。
        record(journal, state, source_rowid=rowid, accepted=state["accepted"]+count, dense_accepted=state["dense_accepted"]+dense,
               batch_size=batch_size, concurrency=concurrency, prepare_seconds=round(prepare_seconds, 3),
               upload_seconds=round(upload_seconds, 3), payload_mb=round(size / 1_000_000, 2), raw_payload_mb=round(raw_size / 1_000_000, 2))

    try:
        while True:
            batch = await asyncio.to_thread(read_batch)
            if batch is None:
                break
            rowid, count, dense, body, prepare_seconds, raw_size = batch
            pending.append((asyncio.create_task(upload(body)), rowid, count, dense, prepare_seconds, len(body), raw_size))
            if len(pending) >= concurrency:
                await acknowledge()
        while pending:
            await acknowledge()
    finally:
        for task, *_ in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*(task for task,*_ in pending), return_exceptions=True)
        source.close()
    # 大批量只等待接收，不在每个请求里等待后台应用；结束阶段单独验收。
    record(journal, state, stage="applying")
    return state


async def verify(db, client, journal, state):
    if state["stage"] == "applying":
        while True:
            info = await client.get_collection(state["collection"])
            if info.optimizer_status != "ok":
                raise RuntimeError(f"Qdrant optimizer failed: {info.optimizer_status}")
            actual = (await client.count(state["collection"], exact=True)).count
            queue = info.update_queue
            record(journal, state, stage="applying", applied=actual, pending_updates=queue.length)
            if actual > state["total"]:
                raise RuntimeError(f"Qdrant count mismatch: expected {state['total']}, got {actual}")
            if actual == state["total"] and queue.length == 0 and not queue.deferred_points:
                break
            await asyncio.sleep(15)
        await client.update_collection(state["collection"], optimizers_config=models.OptimizersConfigDiff(indexing_threshold=10000))
        record(journal, state, stage="indexing", count=actual)
    while True:
        info = await client.get_collection(state["collection"])
        record(journal, state, stage="indexing", indexed=info.indexed_vectors_count)
        if info.optimizer_status != "ok":
            raise RuntimeError(f"Qdrant optimizer failed: {info.optimizer_status}")
        if info.status == models.CollectionStatus.GREEN:
            break
        await asyncio.sleep(15)
    rows = db.execute("SELECT arxiv_id,document FROM arxiv LIMIT 3").fetchall()
    points = await client.retrieve(state["collection"], ids=[point_id(identifier) for identifier,_ in rows], with_payload=True, with_vectors=False)
    actual = {str(point.id):point.payload for point in points}
    for (identifier,_),document in zip(rows,publication_documents(rows),strict=True):
        if actual.get(point_id(identifier)) != point_payload(document):
            raise RuntimeError(f"Qdrant payload verification failed: {identifier}")
    record(journal, state, stage="migration_verify")
    if rows:
        document = publication_documents(rows)[0]
        vectors = publication_vectors(db, rows)
        matches = await QdrantPaperRepository(client,state["collection"]).search(
            query=document["title"], vector=vectors.get(rows[0][0]), categories=[],
            published_after=None,published_before=None,limit=10,
        )
        if not matches:
            raise RuntimeError("Qdrant BM25/hybrid verification returned no candidates")
    record(journal, state, stage="ready_for_cutover")


async def run(args):
    from scholar.core.config.app_settings import settings
    args.journal.parent.mkdir(parents=True,exist_ok=True)
    # 进度响应很小，禁用响应压缩，避免内存紧张时 zstd 解压使等待任务退出；上传仍使用 gzip。
    client = AsyncQdrantClient(url=settings.QDRANT_URL,api_key=settings.QDRANT_API_KEY,cloud_inference=True,trust_env=False,headers={"Accept-Encoding":"identity"},timeout=settings.SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS)
    try:
        # 全程只读快照阻止离线 writer 并发改动基线；看板只读 JSON，不争用此锁。
        with duckdb.connect(str(args.db),read_only=True,config={"memory_limit":"512MB","threads":"1"}) as db:
            async with httpx.AsyncClient(
                base_url=settings.QDRANT_URL, headers={"api-key": settings.QDRANT_API_KEY, "Accept-Encoding":"identity"},
                timeout=settings.SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS, trust_env=False,
                limits=httpx.Limits(max_connections=args.concurrency, max_keepalive_connections=args.concurrency),
            ) as http:
                state = await migrate(db,client,http,args.journal,batch_size=args.batch_size,resume=args.resume,concurrency=args.concurrency)
            if state["stage"] == "migration_complete":
                return state
            await verify(db,client,args.journal,state)
        if args.cutover:
            with open_database(args.db,memory_limit="512MB",threads=1) as db:
                record(args.journal,state,stage="migration_switch",alias=settings.SCHOLAR_COLLECTION_ALIAS)
                await switch_alias(client,settings.SCHOLAR_COLLECTION_ALIAS,state["collection"])
                # 远端验证和切换完成后，后续同步只写同一混合集合；旧库保留供人工回退。
                db.begin()
                try:
                    db.execute("UPDATE import_state SET target_collection=?,published=true",[state["collection"]])
                    db.execute("DELETE FROM pending_publish")
                    db.execute("DELETE FROM pending_delete")
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
            record(args.journal,state,stage="migration_complete")
        return state
    finally:
        await client.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,default=SERVICE_ROOT/".runtime/scholar.duckdb")
    parser.add_argument("--journal",type=Path,default=SERVICE_ROOT/".runtime/qdrant-hybrid-migration.json")
    parser.add_argument("--batch-size",type=int,default=2048)
    parser.add_argument("--concurrency",type=int,default=4)
    parser.add_argument("--resume",action="store_true")
    parser.add_argument("--cutover",action="store_true",help="验收后切换 Qdrant 读别名和离线发布目标；保留旧集合和 ES")
    args=parser.parse_args()
    if args.batch_size<1:
        parser.error("batch-size must be positive")
    if args.concurrency<1:
        parser.error("concurrency must be positive")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

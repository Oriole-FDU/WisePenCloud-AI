"""为现有 Qdrant 集合回填完整标题 keyword 字段；不读取或重算向量。"""

import argparse
import asyncio
import json
from pathlib import Path
from time import perf_counter

import httpx

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.common.client_exceptions import ResourceExhaustedResponse
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from scholar.core.config.bootstrap_settings import SERVICE_ROOT
from scholar.utils import normalize_title


async def _request(stage, method, *args, **kwargs):
    """只重放临时网络/服务失败；所有调用均只读或幂等写入派生标题字段。"""
    for attempt in range(4):
        try:
            return await method(*args, **kwargs)
        except (ResponseHandlingException, UnexpectedResponse, ResourceExhaustedResponse) as error:
            source = error.source if isinstance(error, ResponseHandlingException) else error
            transient = (
                isinstance(source, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError))
                or isinstance(error, ResourceExhaustedResponse)
                or isinstance(error, UnexpectedResponse) and error.status_code in (429, 502, 503, 504)
            )
            detail = f"{type(source).__name__}: {source}"
            if not transient or attempt == 3:
                error.add_note(f"Title backfill {stage} failed: {detail}")
                raise
            delay = max(2 ** attempt, error.retry_after_s) if isinstance(error, ResourceExhaustedResponse) else 2 ** attempt
            print(json.dumps({"stage": stage, "retry": attempt + 1, "delay_seconds": delay, "error": detail}), flush=True)
            await asyncio.sleep(delay)


async def _write_batch(client, collection, points):
    """只提交派生字段更新并确认接收；不在每批后回读或等待后台应用。"""
    if not points:
        return 0.0
    operations = [models.SetPayloadOperation(set_payload=models.SetPayload(
        payload={"normalized_title": normalize_title(point.payload["title"])}, points=[point.id],
    )) for point in points]
    started = perf_counter()
    results = await _request("submit", client.batch_update_points, collection, update_operations=operations, wait=False)
    if any(result.status not in (models.UpdateStatus.COMPLETED, models.UpdateStatus.ACKNOWLEDGED) for result in results):
        raise RuntimeError("title payload batch was not accepted")
    return perf_counter() - started


def _save(journal, state):
    temporary = journal.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    temporary.replace(journal)


async def verify(client, collection: str, journal: Path):
    """显式执行一次阶段末验收；不轮询、不建索引、不等待 HNSW。"""
    state = json.loads(journal.read_text(encoding="utf-8"))
    if state["collection"] != collection or state["stage"] != "submitted":
        raise ValueError("title verification requires a fully submitted journal for this collection")
    info = await _request("verify_queue", client.get_collection, collection)
    if info.update_queue.length or info.update_queue.deferred_points:
        raise RuntimeError("Qdrant still has pending updates; title backfill is not verified")
    indexed = (await _request("verify_titles", client.count, collection, exact=True, count_filter=models.Filter(must_not=[
        models.IsEmptyCondition(is_empty=models.PayloadField(key="normalized_title")),
    ]))).count
    if indexed != info.points_count:
        raise RuntimeError(f"title field count mismatch: {indexed} / {info.points_count}")
    state["stage"] = "verified"
    _save(journal, state)
    print(json.dumps(state, ensure_ascii=False), flush=True)
    return state


async def backfill(client, collection: str, journal: Path, *, batch_size: int = 128, resume: bool = False,
                   concurrency: int = 1, max_batches: int | None = None):
    state = json.loads(journal.read_text(encoding="utf-8")) if resume else {
        "collection": collection, "cursor": None, "accepted": 0, "stage": "upload",
    }
    if state["collection"] != collection:
        raise ValueError("title journal belongs to a different collection")
    # 旧断点记录的是已应用数量，也是已接收前缀；只转换字段，不重新扫描历史数据。
    if "updated" in state:
        state["accepted"] = state.pop("updated")
        state["stage"] = "verified" if state.pop("complete") else "upload"
    if state["stage"] != "upload":
        print(json.dumps(state, ensure_ascii=False), flush=True)
        return state
    if not resume and journal.exists():
        raise ValueError("journal already exists; use --resume or a new journal path")
    journal.parent.mkdir(parents=True, exist_ok=True)
    started, initial_accepted = perf_counter(), state["accepted"]
    cursor, stop_reading, batches = state["cursor"], False, 0
    pending = []
    try:
        while not stop_reading or pending:
            if not stop_reading and len(pending) < concurrency:
                read_started = perf_counter()
                points, cursor = await _request(
                    "scroll", client.scroll, collection, offset=cursor, limit=batch_size,
                    with_payload=["title"], with_vectors=False,
                )
                pending.append((asyncio.create_task(_write_batch(client, collection, points)),
                                cursor, len(points), perf_counter() - read_started))
                batches += 1
                stop_reading = cursor is None or max_batches is not None and batches >= max_batches
                continue

            task, accepted_cursor, count, scroll_seconds = pending[0]
            submit_seconds = await task
            pending.pop(0)
            # 断点只声明服务端接收的连续前缀；不把接收确认当作已经应用或索引完成。
            state.update(cursor=accepted_cursor, accepted=state["accepted"] + count)
            if accepted_cursor is None:
                state["stage"] = "submitted"
            _save(journal, state)
            elapsed = perf_counter() - started
            print(json.dumps(state | {
                "scroll_seconds": round(scroll_seconds, 3), "submit_seconds": round(submit_seconds, 3),
                "elapsed_seconds": round(elapsed, 3),
                "accepted_per_second": round((state["accepted"] - initial_accepted) / elapsed, 2),
            }, ensure_ascii=False), flush=True)
    finally:
        for task, *_ in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*(task for task, *_ in pending), return_exceptions=True)
    return state


async def run(args):
    from scholar.core.config.app_settings import settings

    client = AsyncQdrantClient(
        url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY, trust_env=False,
        # SDK 的同步版本探测使用独立 httpx.get，不继承 trust_env=False；避免额外代理路径。
        check_compatibility=False,
        timeout=settings.SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS,
        # 已有迁移出现过 zstd 解压分配失败；小型标题响应不需要压缩，连接留给有界流水线复用。
        headers={"Accept-Encoding": "identity"},
        limits=httpx.Limits(max_connections=args.concurrency + 1, max_keepalive_connections=args.concurrency + 1, keepalive_expiry=30),
    )
    try:
        target = args.collection or settings.SCHOLAR_COLLECTION_ALIAS
        aliases = await _request("aliases", client.get_aliases)
        collection = next((alias.collection_name for alias in aliases.aliases if alias.alias_name == target), target)
        if args.verify:
            return await verify(client, collection, args.journal)
        return await backfill(client, collection, args.journal, batch_size=args.batch_size, resume=args.resume,
                              concurrency=args.concurrency, max_batches=args.max_batches)
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", help="真实集合或读取别名；断点固定真实集合")
    parser.add_argument("--journal", type=Path, default=SERVICE_ROOT / ".runtime/title-backfill.json")
    parser.add_argument("--batch-size", type=int, default=128, help="每个写入批次的标题数，默认 128")
    parser.add_argument("--concurrency", type=int, default=1, help="同时在途的批次数，默认 1；实测高并发会加重写入争用")
    parser.add_argument("--max-batches", type=int, help="本次最多处理的批次数，便于小规模验收")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--verify", action="store_true", help="只执行一次阶段末验收，不提交数据或创建索引")
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("batch-size must be positive")
    if args.concurrency < 1:
        parser.error("concurrency must be positive")
    if args.max_batches is not None and args.max_batches < 1:
        parser.error("max-batches must be positive")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

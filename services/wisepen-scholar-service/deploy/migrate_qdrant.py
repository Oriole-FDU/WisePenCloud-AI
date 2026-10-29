"""复用 DuckDB 已验证向量灌入 Qdrant；成功批次记录游标，断线可原地续传。"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx
import numpy as np
import orjson
from qdrant_client import AsyncQdrantClient, models

from scholar.core.config.bootstrap_settings import SERVICE_ROOT
from scholar.core.storage.qdrant.paper_vectors import (
    COLLECTION, ensure_collection, point_id, point_payload,
)
from scholar.ml.specter import MODEL_SIGNATURE


def record(path: Path, state: dict, **changes) -> None:
    """只在远端确认成功后推进游标；未知结果重放同一批 UUID 即可。"""
    state.update(changes, updated_at=datetime.now(UTC).isoformat())
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf8')
    temporary.replace(path)
    print(json.dumps(state, ensure_ascii=False), flush=True)


async def migrate(db, client, http, journal: Path, *, batch_size: int, resume: bool):
    state = json.loads(journal.read_text(encoding='utf8')) if resume else {
        'collection': COLLECTION, 'model_signature': MODEL_SIGNATURE,
        'written': 0, 'after': '', 'stage': 'upload',
        'total': db.execute('SELECT count(*) FROM arxiv').fetchone()[0],
    }
    if state['collection'] != COLLECTION or state['model_signature'] != MODEL_SIGNATURE:
        raise ValueError('migration journal uses a different model')
    # 上传验收后的续跑直接进入收尾，避免重新关闭已经构建完成的向量索引。
    if state['stage'] != 'upload':
        return state
    await ensure_collection(client)
    await client.update_collection(COLLECTION, optimizers_config=models.OptimizersConfigDiff(indexing_threshold=0))
    # 兼容本次已确认的旧 ID 游标；之后只顺序扫描一次单表，不再逐批排序/关联全库。
    baseline = state.setdefault('baseline_after', state['after'])
    record(journal, state, stage='upload')
    source = db.cursor()
    source.execute("""
        SELECT rowid,arxiv_id,document->'categories',document->>'submitted_date'
        FROM arxiv WHERE rowid>? AND arxiv_id>?
    """, [state.get('source_rowid', -1), baseline])

    def read_batch():
        rows = source.fetchmany(batch_size)
        if not rows:
            return None
        ids = [row[1] for row in rows]
        vectors = dict(db.execute("""
            SELECT arxiv_id,vector FROM paper_embeddings
            WHERE arxiv_id BETWEEN ? AND ? AND arxiv_id IN (SELECT unnest(?))
        """, [min(ids), max(ids), ids]).fetchall())
        body = {'batch': {
            'ids': [point_id(identifier) for identifier in ids],
            'vectors': np.asarray([vectors[identifier] for identifier in ids], dtype=np.float32),
            'payloads': [point_payload({
                'arxiv_id': row[1], 'categories': orjson.loads(row[2]), 'submitted_date': row[3],
            }) for row in rows],
        }}
        return rows, orjson.dumps(body, option=orjson.OPT_SERIALIZE_NUMPY)

    async def upload(body):
        # 断点与最终计数依赖写入已应用；wait=false 只确认接收，尾批可能仍在队列中。
        response = await http.put(
            f'/collections/{COLLECTION}/points', params={'wait': 'true'}, content=body,
            headers={'Content-Type': 'application/json'},
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise RuntimeError(f'Qdrant bulk write failed: {response.text}') from error

    pending = []
    try:
        while True:
            batch = await asyncio.to_thread(read_batch)
            if batch is None:
                break
            rows, body = batch
            pending.append((asyncio.create_task(upload(body)), rows[-1][0], rows[-1][1], len(rows)))
            if len(pending) >= 4:
                task, rowid, after, count = pending.pop(0)
                await task
                record(journal, state, source_rowid=rowid, after=after, written=state['written']+count)
        for task, rowid, after, count in pending:
            await task
            record(journal, state, source_rowid=rowid, after=after, written=state['written']+count)
    finally:
        # 写入结果未知时不推进游标；同一 UUID 重放覆盖即可，不重算向量。
        for task, *_ in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*(task for task, *_ in pending), return_exceptions=True)
        source.close()
    actual = (await client.count(COLLECTION, exact=True)).count
    if actual != state['total']:
        raise RuntimeError(f"Qdrant upload incomplete: expected {state['total']}, got {actual}")
    await client.update_collection(COLLECTION, optimizers_config=models.OptimizersConfigDiff(indexing_threshold=10000))
    record(journal, state, stage='indexing', count=actual)
    return state


async def finish(db_path, client, settings, journal, state, args):
    """索引就绪后切回无向量 ES，重启在线服务，最后删除指定的向量索引。"""
    while True:
        info = await client.get_collection(COLLECTION)
        record(journal, state, stage='indexing', indexed=info.indexed_vectors_count)
        if info.optimizer_status != 'ok':
            raise RuntimeError(f'Qdrant optimizer failed: {info.optimizer_status}')
        if info.status == models.CollectionStatus.GREEN and info.indexed_vectors_count:
            break
        await asyncio.sleep(30)

    from scholar.core.storage.elasticsearch.paper_repository import create_elasticsearch
    alias = settings.SCHOLAR_INDEX_ALIAS
    async with create_elasticsearch(settings.ELASTICSEARCH_URIS, settings.ELASTICSEARCH_USERNAME,
                                    settings.ELASTICSEARCH_PASSWORD, 120) as es:
        current = set(await es.indices.get_alias(name=alias))
        if current not in ({args.vector_index}, {args.keyword_index}):
            raise RuntimeError(f'ES alias ownership changed: {current}')
        record(journal, state, stage='cutover', keyword_index=args.keyword_index,
               vector_index=args.vector_index)
        # 两端投影来自同一离线基线；先恢复旧索引写入，再明确切换读别名和发布目标。
        await es.indices.put_settings(index=args.keyword_index, settings={'index.blocks.write':False})
        if current != {args.keyword_index}:
            await es.indices.update_aliases(actions=[
                {'remove':{'index':args.vector_index,'alias':alias}},
                {'add':{'index':args.keyword_index,'alias':alias}},
            ])
        with duckdb.connect(str(db_path), config={'memory_limit':'4GB','threads':'4'}) as db:
            db.execute('UPDATE import_state SET target_index=?', [args.keyword_index])
        if args.service_pid:
            record(journal, state, stage='service_restart')
            # Windows 无窗口 Python 服务需要 /F；PID 来自正在提供 Scholar 的进程。
            stopped = subprocess.run(['taskkill','/F','/PID',str(args.service_pid)],
                                     capture_output=True, text=True, encoding='oem')
            if stopped.returncode:
                raise RuntimeError(f'Scholar service could not be stopped; vector index retained: {stopped.stderr.strip()}')
            log = (SERVICE_ROOT/'.runtime/service-qdrant.log').open('ab', buffering=0)
            process = subprocess.Popen([sys.executable,'-m','scholar.main'], cwd=SERVICE_ROOT.parent.parent,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            log.close()
            async with httpx.AsyncClient(timeout=5, trust_env=False,
                    headers={'X-From-Source':settings.FROM_SOURCE_SECRET}) as http:
                for _ in range(60):
                    if process.poll() is not None:
                        raise RuntimeError('Scholar restart failed; vector index retained; see service-qdrant.log')
                    try:
                        ready = await http.get(f'http://127.0.0.1:{args.service_port}/scholar/health')
                    except (httpx.ConnectError, httpx.ReadError):
                        pass  # 启动及旧监听连接关闭期间，尚未建立可用的 HTTP 会话。
                    else:
                        if ready.status_code == 200:
                            break
                    await asyncio.sleep(2)
                else:
                    raise RuntimeError('Scholar did not become ready; vector index retained')
        record(journal, state, stage='delete_vector_index')
        await es.indices.delete(index=args.vector_index)
        record(journal, state, stage='migration_complete')


async def run(args):
    from scholar.core.config.app_settings import settings

    args.journal.parent.mkdir(parents=True, exist_ok=True)
    client = AsyncQdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY, timeout=120, trust_env=False)
    try:
        with duckdb.connect(str(args.db), read_only=True, config={'memory_limit':'256MB', 'threads':'1'}) as db:
            async with httpx.AsyncClient(base_url=settings.QDRANT_URL, headers={'api-key':settings.QDRANT_API_KEY}, timeout=120, trust_env=False) as http:
                state = await migrate(db, client, http, args.journal, batch_size=args.batch_size, resume=args.resume)
        if args.keyword_index and state['stage'] != 'migration_complete':
            await finish(args.db, client, settings, args.journal, state, args)
        return state
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=SERVICE_ROOT/'.runtime/scholar.duckdb')
    parser.add_argument('--journal', type=Path, default=SERVICE_ROOT/'.runtime/qdrant-migration.json')
    parser.add_argument('--batch-size', type=int, default=2048)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--keyword-index', help='灌库和索引构建完成后回切的无向量 ES 索引')
    parser.add_argument('--vector-index', help='回切后删除的 ES 向量索引')
    parser.add_argument('--service-pid', type=int, help='切流时重启的当前 Scholar 进程')
    parser.add_argument('--service-port', type=int, default=19916)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error('batch-size must be positive')
    if bool(args.keyword_index) != bool(args.vector_index):
        parser.error('keyword-index and vector-index must be specified together')
    if args.keyword_index and args.keyword_index == args.vector_index:
        parser.error('keyword-index and vector-index must differ')
    asyncio.run(run(args))


if __name__ == '__main__':
    main()

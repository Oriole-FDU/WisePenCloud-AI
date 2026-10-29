"""论文向量派生表与可恢复 GPU 批处理；DuckDB 仍只有协调进程一个 writer。"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import orjson

from scholar.ml.specter import DEFAULT_MODELS, MODEL_SIGNATURE

# 指纹直接在 DuckDB 计算，供续跑、增量失效判断和发布复用；只包含标题与摘要。
INPUT_HASH_SQL = "sha256((a.document->>'title') || chr(0) || (a.document->>'abstract'))"
EMBEDDING_SCHEMA = """
                   CREATE TABLE IF NOT EXISTS paper_embeddings
                   (
                       arxiv_id
                       VARCHAR
                       PRIMARY
                       KEY,
                       model_signature
                       VARCHAR
                       NOT
                       NULL,
                       input_fingerprint
                       VARCHAR
                       NOT
                       NULL,
                       vector
                       FLOAT
                   [
                       768]
                       NOT
                       NULL
                   );
                   CREATE TABLE IF NOT EXISTS pending_embeddings
                   (
                       arxiv_id
                       VARCHAR
                       PRIMARY
                       KEY
                   ); \
                   """


def reconcile_embeddings(db, arxiv_ids: list[str] | None = None):
    """全量启动只扫描一次；正常导入/同步仅检查本批变更论文。"""
    selection = "" if arxiv_ids is None else "WHERE a.arxiv_id IN (SELECT unnest(?))"
    params = [] if arxiv_ids is None else [arxiv_ids]
    db.execute(
        f"""
        INSERT OR IGNORE INTO pending_embeddings
        SELECT a.arxiv_id FROM arxiv a LEFT JOIN paper_embeddings e USING(arxiv_id)
        {selection} {"WHERE" if arxiv_ids is None else "AND"} (
            e.arxiv_id IS NULL OR e.model_signature <> ? OR e.input_fingerprint <> {INPUT_HASH_SQL}
        )
    """,
        [*params, MODEL_SIGNATURE],
    )
    # 不删除可重用的有效向量；未重新编码前，publisher 通过输入指纹排除失效向量。


def publication_documents(rows) -> list[dict]:
    """读取仅供 Elasticsearch 全文索引使用的 arXiv 文档，不投影派生向量。"""
    return [orjson.loads(document) for _, document in rows]


def publication_vectors(db, rows) -> dict[str, tuple[float, ...]]:
    """批量读取本批中仍匹配当前标题与摘要指纹的 DuckDB 向量。"""
    return dict(
        db.execute(
            f"""
        SELECT a.arxiv_id,e.vector FROM arxiv a JOIN paper_embeddings e USING(arxiv_id)
        WHERE a.arxiv_id IN (SELECT unnest(?)) AND e.model_signature=?
            AND e.input_fingerprint={INPUT_HASH_SQL}
    """,
            [[arxiv_id for arxiv_id, _ in rows], MODEL_SIGNATURE],
        ).fetchall()
    )


class DocumentWorker:
    """GPU 进程只接触论文输入；不向子进程传递数据库、ES 或 Nacos 凭证。"""

    def __init__(self, process):
        self.process = process

    async def encode(self, documents: list[dict]) -> list[list[float]]:
        self.process.stdin.write(orjson.dumps(documents) + b"\n")
        await self.process.stdin.drain()
        line = await self.process.stdout.readline()
        if not line:
            raise RuntimeError(
                f"GPU encoding worker exited: {await self.process.wait()}"
            )
        return orjson.loads(line)["vectors"]


@asynccontextmanager
async def document_worker(python: Path, models: Path = DEFAULT_MODELS):
    source_root = Path(__file__).resolve().parents[2] / "src"
    # 显式使用提供的 GPU interpreter；不存在或没有 CUDA 时直接失败，不改主环境。
    process = await asyncio.create_subprocess_exec(
        str(python),
        "-u",
        "-m",
        "scholar.ml.specter",
        "document-worker",
        "--models",
        str(models),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(source_root)},
        limit=16 * 1024 * 1024,
    )
    try:
        line = await process.stdout.readline()
        if not line:
            raise RuntimeError(f"GPU worker failed to start: {await process.wait()}")
        ready = orjson.loads(line)
        if ready["signature"] != MODEL_SIGNATURE:
            raise RuntimeError("GPU worker and coordinator model signatures differ")
        yield DocumentWorker(process)
    finally:
        if process.returncode is None:
            process.terminate()
        await process.wait()


async def generate_embeddings(
    db, worker, *, batch_size=8, max_batches=None, progress=None, mark_publish=True
):
    """GPU 小批推理，最多 256 篇一起持久提交；失败时只重做未提交的一组。"""
    reconcile_embeddings(db)
    remaining = db.execute("SELECT count(*) FROM pending_embeddings").fetchone()[0]
    total, written, batches = remaining, 0, 0
    after = ""
    while remaining and (max_batches is None or batches < max_batches):
        # 先取小批 ID 再限定来源范围，避免每批重新扫描数百万份 JSON。
        rows = db.execute(
            f"""
            WITH pending AS MATERIALIZED (
                SELECT arxiv_id FROM pending_embeddings WHERE arxiv_id>? ORDER BY arxiv_id LIMIT 256
            )
            SELECT a.arxiv_id, a.document->>'title', a.document->>'abstract',
                {INPUT_HASH_SQL}
            FROM pending p JOIN arxiv a USING(arxiv_id)
            WHERE a.arxiv_id BETWEEN (SELECT min(arxiv_id) FROM pending) AND (SELECT max(arxiv_id) FROM pending)
            ORDER BY p.arxiv_id
        """,
            [after],
        ).fetchall()
        encoded = []
        for offset in range(0, len(rows), batch_size):
            inputs = rows[offset: offset + batch_size]
            vectors = await worker.encode(
                [{"title": row[1], "abstract": row[2]} for row in inputs]
            )
            encoded.extend(
                {"id": row[0], "fingerprint": row[3], "vector": vector}
                for row, vector in zip(inputs, vectors, strict=True)
            )
        db.begin()
        try:
            payload = orjson.dumps(encoded).decode()
            db.execute(
                """
                INSERT OR REPLACE INTO paper_embeddings
                SELECT value->>'id', ?, value->>'fingerprint', (value->'vector')::FLOAT[768]
                FROM json_each(?)
            """,
                [MODEL_SIGNATURE, payload],
            )
            if mark_publish:
                db.execute(
                    "INSERT OR IGNORE INTO pending_publish SELECT value->>'id' FROM json_each(?)",
                    [payload],
                )
            db.execute(
                "DELETE FROM pending_embeddings WHERE arxiv_id IN (SELECT value->>'id' FROM json_each(?))",
                [payload],
            )
            db.commit()
        except BaseException:
            db.rollback()
            raise
        after = rows[-1][0]
        written += len(rows)
        remaining -= len(rows)
        batches += 1
        event = {
            "stage": "embedding_encode",
            "total": total,
            "written": written,
            "remaining": remaining,
            "failed": 0,
        }
        if progress is not None:
            progress(event)
        else:
            print(json.dumps(event), flush=True)
    return {"written": written, "remaining": remaining}

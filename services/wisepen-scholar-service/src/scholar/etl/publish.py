"""版本索引的逐项发布与恢复；完整检查通过前不修改线上别名。"""

from uuid import uuid4

import duckdb
import orjson
from elasticsearch import AsyncElasticsearch

from scholar.core.storage.elasticsearch.paper_repository import ARXIV_MAPPING
from scholar.etl.arxiv import database_status


async def publish_arxiv(
    db: duckdb.DuckDBPyConnection,
    es: AsyncElasticsearch,
    alias: str,
    *,
    batch_size: int = 5000,
    max_batches: int | None = None,
) -> dict:
    """网络未知结果可按同一文档 ID 重放，成功项才从 pending 移除。"""
    row = db.execute(
        "SELECT complete,target_index,published FROM import_state"
    ).fetchone()
    if row is None or not row[0]:
        raise ValueError("arXiv import must reach EOF before publication")
    target, published = row[1:]
    if published:
        return database_status(db)
    if target is None:
        target = f"{alias}-{uuid4().hex}"
        db.execute("UPDATE import_state SET target_index=?", [target])
    if not await es.indices.exists(index=target):
        # 创建失败可重试；已存在的目标索引绝不删除重建。
        await es.indices.create(
            index=target, mappings=ARXIV_MAPPING, settings={"refresh_interval": "-1"}
        )
    else:
        # 离线富化沿用已发布论文 ID；先增加实际使用的字段，再重放受影响文档。
        await es.indices.put_mapping(
            index=target, properties=ARXIV_MAPPING["properties"]
        )
    batches = 0
    while max_batches is None or batches < max_batches:
        # 先限定待发布 ID，再按范围读取完整文档，避免每一批都排序整库 JSON。
        rows = db.execute(
            """WITH batch AS MATERIALIZED (
                SELECT arxiv_id FROM pending_publish ORDER BY arxiv_id LIMIT ?
            ) SELECT a.arxiv_id,a.document FROM arxiv a JOIN batch USING(arxiv_id)
            WHERE a.arxiv_id BETWEEN (SELECT min(arxiv_id) FROM batch)
                AND (SELECT max(arxiv_id) FROM batch)
            ORDER BY a.arxiv_id""",
            [batch_size],
        ).fetchall()
        if not rows:
            break
        operations = []
        for arxiv_id, document in rows:
            operations.extend(
                [{"index": {"_index": target, "_id": arxiv_id}}, orjson.loads(document)]
            )
        response = await es.bulk(operations=operations)
        succeeded, failed = [], []
        for (arxiv_id, _), item in zip(rows, response["items"], strict=True):
            result = item["index"]
            if 200 <= result["status"] < 300:
                succeeded.append(arxiv_id)
            else:
                failed.append({"arxiv_id": arxiv_id, "error": result["error"]})
        if succeeded:
            db.execute(
                "DELETE FROM pending_publish WHERE arxiv_id IN (SELECT json_extract_string(value,'$') FROM json_each(?))",
                [orjson.dumps(succeeded).decode()],
            )
        if failed:
            raise RuntimeError(
                f"ES bulk failed; successful items acknowledged, remaining items replayable: {failed[:3]}"
            )
        batches += 1
        print(
            orjson.dumps(
                {
                    "stage": "publish",
                    "index": target,
                    "pending": db.execute(
                        "SELECT count(*) FROM pending_publish"
                    ).fetchone()[0],
                }
            ).decode(),
            flush=True,
        )
    if db.execute("SELECT count(*) FROM pending_publish").fetchone()[0]:
        return database_status(db)
    await es.indices.refresh(index=target)
    expected = db.execute("SELECT count(*) FROM arxiv").fetchone()[0]
    actual = (await es.count(index=target))["count"]
    if actual != expected:
        raise RuntimeError(f"index count mismatch: expected {expected}, got {actual}")
    sample = db.execute(
        "SELECT arxiv_id,document FROM arxiv ORDER BY arxiv_id LIMIT 3"
    ).fetchall()
    result = await es.mget(index=target, ids=[r[0] for r in sample])
    for (_, document), item in zip(sample, result["docs"], strict=True):
        if not item["found"] or item["_source"] != orjson.loads(document):
            raise RuntimeError("index sample does not match DuckDB projection")
    await es.indices.put_settings(index=target, settings={"refresh_interval": "1s"})
    actions = [
        {"remove": {"index": "*", "alias": alias, "must_exist": False}},
        {"add": {"index": target, "alias": alias}},
    ]
    await es.indices.update_aliases(actions=actions)
    # 别名切换后崩溃会重做相同验证和切换，不能提前声称发布完成。
    db.execute("UPDATE import_state SET published=true")
    return database_status(db)

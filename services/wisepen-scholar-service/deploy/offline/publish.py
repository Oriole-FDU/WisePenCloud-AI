"""版本索引的逐项发布与恢复；完整检查通过前不修改线上别名。"""

from __future__ import annotations

from uuid import uuid4

import duckdb
import orjson
from elasticsearch import AsyncElasticsearch
from offline.arxiv import database_status
from offline.embeddings import publication_documents, publication_vectors
from qdrant_client import AsyncQdrantClient, models

from scholar.core.storage.qdrant.paper_vectors import (
    COLLECTION,
    ensure_collection,
    point_id,
    point_payload,
)
from scholar.core.storage.elasticsearch.paper_repository import (
    ARXIV_INDEX_SETTINGS,
    ARXIV_MAPPING,
)


def _publication_status(db: duckdb.DuckDBPyConnection) -> dict:
    status = database_status(db)
    status["pending_delete"] = db.execute(
        "SELECT count(*) FROM pending_delete"
    ).fetchone()[0]
    return status


async def publish_arxiv(
    db: duckdb.DuckDBPyConnection,
    es: AsyncElasticsearch,
    alias: str,
    *,
    qdrant: AsyncQdrantClient,
    batch_size: int = 5000,
    max_batches: int | None = None,
) -> dict:
    """双写 ES 全文事实与 Qdrant 向量；两端成功后才确认 DuckDB 待办。"""
    row = db.execute(
        "SELECT complete,target_index,published FROM import_state"
    ).fetchone()
    if row is None or not row[0]:
        raise ValueError("arXiv import must reach EOF before publication")
    target, published = row[1:]
    await ensure_collection(qdrant)

    if published:
        if not target:
            raise ValueError("published arXiv state is missing its target index")
        batches = 0
        while max_batches is None or batches < max_batches:
            rows = db.execute(
                """WITH pending AS MATERIALIZED (
                SELECT arxiv_id
                FROM pending_publish
                UNION
                SELECT arxiv_id
                FROM pending_delete )
                SELECT p.arxiv_id,
                       CASE WHEN d.arxiv_id IS NULL THEN 'index' ELSE 'delete' END,
                       a.document
                FROM pending p
                         LEFT JOIN pending_delete d USING (arxiv_id)
                         LEFT JOIN arxiv a USING (arxiv_id)
                ORDER BY p.arxiv_id LIMIT ?""",
                [batch_size],
            ).fetchall()
            if not rows:
                break

            indexed = [
                (arxiv_id, document)
                for arxiv_id, action, document in rows
                if action == "index"
            ]
            documents = publication_documents(indexed)
            projected = iter(documents)
            operations = []
            for arxiv_id, action, document in rows:
                if action == "delete":
                    operations.append({"delete": {"_index": target, "_id": arxiv_id}})
                else:
                    operations.extend(
                        [
                            {"index": {"_index": target, "_id": arxiv_id}},
                            next(projected),
                        ]
                    )

            response = await es.bulk(operations=operations)
            succeeded, failed = [], []
            for (arxiv_id, action, _), item in zip(
                rows, response["items"], strict=True
            ):
                result = item[action]
                status = result["status"]
                # 删除重放可能遇到上次已成功但尚未确认的 404。
                if 200 <= status < 300 or (action == "delete" and status == 404):
                    succeeded.append((arxiv_id, action))
                else:
                    failed.append(
                        {
                            "arxiv_id": arxiv_id,
                            "action": action,
                            "error": result.get("error"),
                        }
                    )

            vectors = publication_vectors(db, indexed)
            documents_by_id = dict(
                zip((arxiv_id for arxiv_id, _ in indexed), documents, strict=True)
            )
            qdrant_deletes = [
                point_id(arxiv_id)
                for arxiv_id, action, _ in rows
                if action == "delete" or arxiv_id not in vectors
            ]
            if qdrant_deletes:
                await qdrant.delete(
                    collection_name=COLLECTION,
                    points_selector=models.PointIdsList(points=qdrant_deletes),
                    wait=True,
                )
            points = [
                models.PointStruct(
                    id=point_id(arxiv_id),
                    vector=list(vector),
                    payload=point_payload(documents_by_id[arxiv_id]),
                )
                for arxiv_id, vector in vectors.items()
            ]
            if points:
                await qdrant.upsert(
                    collection_name=COLLECTION, points=points, wait=True
                )

            # ES 与 Qdrant 不共享事务；只有当前批两端都完成后才确认 ES 成功项。
            if succeeded:
                db.begin()
                try:
                    for arxiv_id, action in succeeded:
                        db.execute(
                            "DELETE FROM pending_publish WHERE arxiv_id=?", [arxiv_id]
                        )
                        if action == "delete":
                            db.execute(
                                "DELETE FROM pending_delete WHERE arxiv_id=?",
                                [arxiv_id],
                            )
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise

            if failed:
                raise RuntimeError(
                    f"incremental ES bulk failed; failed items remain pending: {failed[:3]}"
                )

            batches += 1
            print(
                orjson.dumps(
                    {
                        "stage": "publish-incremental",
                        "index": target,
                        "indexed": sum(action == "index" for _, action in succeeded),
                        "deleted": sum(action == "delete" for _, action in succeeded),
                        "pending_publish": db.execute(
                            "SELECT count(*) FROM pending_publish"
                        ).fetchone()[0],
                        "pending_delete": db.execute(
                            "SELECT count(*) FROM pending_delete"
                        ).fetchone()[0],
                    }
                ).decode(),
                flush=True,
            )
        return _publication_status(db)

    if target is None:
        target = f"{alias}-{uuid4().hex}"
        db.execute("UPDATE import_state SET target_index=?", [target])
    if not await es.indices.exists(index=target):
        # 创建失败可重试；已存在的目标索引绝不删除重建。
        await es.indices.create(
            index=target,
            mappings=ARXIV_MAPPING,
            settings={**ARXIV_INDEX_SETTINGS, "refresh_interval": "-1"},
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
            SELECT arxiv_id
            FROM pending_publish
            ORDER BY arxiv_id LIMIT ?
            )
            SELECT a.arxiv_id, a.document
            FROM arxiv a
                     JOIN batch USING (arxiv_id)
            WHERE a.arxiv_id BETWEEN (SELECT min(arxiv_id) FROM batch)
                      AND (SELECT max(arxiv_id) FROM batch)
            ORDER BY a.arxiv_id""",
            [batch_size],
        ).fetchall()
        if not rows:
            break
        documents = publication_documents(rows)
        operations = []
        for (arxiv_id, _), document in zip(rows, documents, strict=True):
            operations.extend(
                [{"index": {"_index": target, "_id": arxiv_id}}, document]
            )
        response = await es.bulk(operations=operations)
        succeeded, failed = [], []
        for (arxiv_id, _), item in zip(rows, response["items"], strict=True):
            result = item["index"]
            if 200 <= result["status"] < 300:
                succeeded.append(arxiv_id)
            else:
                failed.append({"arxiv_id": arxiv_id, "error": result["error"]})

        vectors = publication_vectors(db, rows)
        documents_by_id = dict(
            zip((arxiv_id for arxiv_id, _ in rows), documents, strict=True)
        )
        missing_vectors = [arxiv_id for arxiv_id, _ in rows if arxiv_id not in vectors]
        if missing_vectors:
            await qdrant.delete(
                collection_name=COLLECTION,
                points_selector=models.PointIdsList(
                    points=[point_id(arxiv_id) for arxiv_id in missing_vectors]
                ),
                wait=True,
            )
        points = [
            models.PointStruct(
                id=point_id(arxiv_id),
                vector=list(vector),
                payload=point_payload(documents_by_id[arxiv_id]),
            )
            for arxiv_id, vector in vectors.items()
        ]
        if points:
            await qdrant.upsert(collection_name=COLLECTION, points=points, wait=True)

        if succeeded:
            db.begin()
            try:
                db.execute(
                    "DELETE FROM pending_publish WHERE arxiv_id IN (SELECT json_extract_string(value,'$') FROM json_each(?))",
                    [orjson.dumps(succeeded).decode()],
                )
                db.commit()
            except BaseException:
                db.rollback()
                raise
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
        return _publication_status(db)
    while max_batches is None or batches < max_batches:
        deleted = db.execute(
            "SELECT arxiv_id FROM pending_delete ORDER BY arxiv_id LIMIT ?",
            [batch_size],
        ).fetchall()
        if not deleted:
            break
        operations = [
            {"delete": {"_index": target, "_id": arxiv_id}} for (arxiv_id,) in deleted
        ]
        response = await es.bulk(operations=operations)
        succeeded, failed = [], []
        for (arxiv_id,), item in zip(deleted, response["items"], strict=True):
            result = item["delete"]
            status = result["status"]
            # 网络结果未知时，重放可能遇到 ES 已删除、本地尚未确认的 404。
            if 200 <= status < 300 or status == 404:
                succeeded.append(arxiv_id)
            else:
                failed.append({"arxiv_id": arxiv_id, "error": result.get("error")})

        await qdrant.delete(
            collection_name=COLLECTION,
            points_selector=models.PointIdsList(
                points=[point_id(arxiv_id) for (arxiv_id,) in deleted]
            ),
            wait=True,
        )

        if succeeded:
            db.begin()
            try:
                for arxiv_id in succeeded:
                    db.execute(
                        "DELETE FROM pending_delete WHERE arxiv_id=?", [arxiv_id]
                    )
                db.commit()
            except BaseException:
                db.rollback()
                raise
        if failed:
            raise RuntimeError(
                f"full ES delete failed; failed items remain pending: {failed[:3]}"
            )

        batches += 1
        print(
            orjson.dumps(
                {
                    "stage": "publish-delete",
                    "index": target,
                    "deleted": len(succeeded),
                    "pending_delete": db.execute(
                        "SELECT count(*) FROM pending_delete"
                    ).fetchone()[0],
                }
            ).decode(),
            flush=True,
        )
    if db.execute("SELECT count(*) FROM pending_delete").fetchone()[0]:
        return _publication_status(db)
    await es.indices.refresh(index=target)
    expected = db.execute("SELECT count(*) FROM arxiv").fetchone()[0]
    actual = (await es.count(index=target))["count"]
    if actual != expected:
        raise RuntimeError(f"index count mismatch: expected {expected}, got {actual}")
    sample = db.execute(
        "SELECT arxiv_id,document FROM arxiv ORDER BY arxiv_id LIMIT 3"
    ).fetchall()
    result = await es.mget(index=target, ids=[r[0] for r in sample])
    for document, item in zip(
        publication_documents(sample),
        result["docs"],
        strict=True,
    ):
        if not item["found"] or item["_source"] != document:
            raise RuntimeError("index sample does not match DuckDB projection")
    await es.indices.put_settings(index=target, settings={"refresh_interval": "1s"})
    actions = [
        {"remove": {"index": "*", "alias": alias, "must_exist": False}},
        {"add": {"index": target, "alias": alias}},
    ]
    await es.indices.update_aliases(actions=actions)
    # 索引与删除待办均已逐项确认；别名切换成功后再标记为已发布。
    db.begin()
    try:
        # 别名切换后崩溃可重做同一验证和切换，不能提前声称发布完成。
        db.execute("UPDATE import_state SET published=true")
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return _publication_status(db)

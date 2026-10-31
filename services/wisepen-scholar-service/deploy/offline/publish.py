"""Qdrant 单库发布；远端应用成功后才确认 DuckDB 待办。"""

from uuid import NAMESPACE_URL, uuid4, uuid5

import duckdb
import orjson
from qdrant_client import AsyncQdrantClient, models
from offline.arxiv import database_status
from offline.embeddings import publication_documents, publication_vectors
from scholar.core.storage.qdrant.paper_repository import (
    BM25_FIELDS,
    BM25_OPTIONS,
)
from scholar.ml.specter import DIMENSIONS, MODEL_SIGNATURE

COLLECTION = f"wisepen-scholar-hybrid-{MODEL_SIGNATURE[:16]}-v1"


def point_id(arxiv_id: str) -> str:
    """为同一 arXiv 身份生成稳定的 Qdrant UUID。"""
    return str(uuid5(NAMESPACE_URL, f"arxiv:{arxiv_id}"))


def point_payload(document: dict) -> dict:
    """完整论文事实随候选直接返回；日期投影为可建立 datetime 索引的格式。"""
    return {
        **document,
        "submitted_date": f"{document['submitted_date']}T00:00:00Z",
        "category_groups": sorted({value.partition('.')[0] for value in document['categories']}),
    }


def point_vectors(document: dict, dense=None) -> dict:
    """无有效稠密向量的论文仍参加关键词检索和精确 lookup。"""
    tldr = document.get("metadata", {}).get("s2", {}).get("tldr")
    texts = [document["title"], tldr.get("text") if tldr else None, document["abstract"]]
    vectors = {
        name: models.Document(text=text or "", model="Qdrant/bm25", options=BM25_OPTIONS)
        for name, text in zip(BM25_FIELDS, texts, strict=True)
    }
    if dense is not None:
        vectors["dense"] = list(dense)
    return vectors


async def ensure_collection(client: AsyncQdrantClient, collection: str = COLLECTION, *, bulk: bool = False):
    if not await client.collection_exists(collection):
        await client.create_collection(
            collection_name=collection,
            vectors_config={"dense": models.VectorParams(size=DIMENSIONS, distance=models.Distance.EUCLID, on_disk=False)},
            sparse_vectors_config={name: models.SparseVectorParams(modifier=models.Modifier.IDF, index=models.SparseIndexParams(on_disk=True)) for name in BM25_FIELDS},
            hnsw_config=models.HnswConfigDiff(on_disk=False),
            optimizers_config=models.OptimizersConfigDiff(indexing_threshold=0 if bulk else 10000),
            on_disk_payload=True,
        )
        for field, kind in (("arxiv_id", models.PayloadSchemaType.KEYWORD), ("doi", models.PayloadSchemaType.KEYWORD), ("categories", models.PayloadSchemaType.KEYWORD), ("category_groups", models.PayloadSchemaType.KEYWORD), ("submitted_date", models.PayloadSchemaType.DATETIME)):
            await client.create_payload_index(collection, field, kind, wait=True)


async def switch_alias(client: AsyncQdrantClient, alias: str, collection: str):
    existing = await client.get_collection_aliases(collection)
    if any(item.alias_name == alias for item in existing.aliases):
        return
    aliases = await client.get_aliases()
    actions = [models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=alias))] if any(item.alias_name == alias for item in aliases.aliases) else []
    actions.append(models.CreateAliasOperation(create_alias=models.CreateAlias(alias_name=alias, collection_name=collection)))
    await client.update_collection_aliases(change_aliases_operations=actions)


def publication_points(db, rows):
    documents = publication_documents(rows)
    vectors = publication_vectors(db, rows)
    return [models.PointStruct(
        id=point_id(arxiv_id), vector=point_vectors(document, vectors.get(arxiv_id)),
        payload=point_payload(document),
    ) for (arxiv_id, _), document in zip(rows, documents, strict=True)]


async def _qdrant_upsert_chunked(qdrant, collection_name, points, *, chunk_size=512, wait=True):
    for start in range(0, len(points), chunk_size):
        await qdrant.upsert(collection_name=collection_name, points=points[start:start+chunk_size], wait=wait)


async def _qdrant_delete_chunked(qdrant, collection_name, point_ids, *, chunk_size=512, wait=True):
    for start in range(0, len(point_ids), chunk_size):
        await qdrant.delete(collection_name=collection_name, points_selector=models.PointIdsList(points=point_ids[start:start+chunk_size]), wait=wait)


async def publish_arxiv(db: duckdb.DuckDBPyConnection, alias: str, *, qdrant: AsyncQdrantClient, batch_size=512, max_batches=None) -> dict:
    state = db.execute("SELECT complete,target_collection,published FROM import_state").fetchone()
    if state is None or not state[0]:
        raise ValueError("arXiv import must reach EOF before publication")
    target, published = state[1:]
    if target is None:
        target = f"{COLLECTION}-{uuid4().hex}"
        db.execute("UPDATE import_state SET target_collection=?", [target])
    if published and not await qdrant.collection_exists(target):
        raise ValueError("published baseline is not a Qdrant hybrid collection; complete migrate_qdrant.py first")
    await ensure_collection(qdrant, target)
    batches = 0
    while max_batches is None or batches < max_batches:
        rows = db.execute("""WITH pending AS MATERIALIZED (
            SELECT arxiv_id FROM pending_publish UNION SELECT arxiv_id FROM pending_delete
        ), batch AS MATERIALIZED (SELECT arxiv_id FROM pending ORDER BY arxiv_id LIMIT ?)
        SELECT p.arxiv_id, a.document FROM batch p LEFT JOIN arxiv a USING(arxiv_id)
        ORDER BY p.arxiv_id""", [batch_size]).fetchall()
        if not rows:
            break
        present = [(identifier, raw) for identifier, raw in rows if raw is not None]
        removed = [point_id(identifier) for identifier, raw in rows if raw is None]
        if removed:
            await _qdrant_delete_chunked(qdrant, target, removed)
        if present:
            await _qdrant_upsert_chunked(qdrant, target, publication_points(db, present))
        # wait=true 确认完整 batch 已应用；异常或结果未知时保留全部待办，按 UUID 重放。
        ids = [identifier for identifier, _ in rows]
        db.begin()
        try:
            db.execute("DELETE FROM pending_publish WHERE arxiv_id IN (SELECT unnest(?))", [ids])
            db.execute("DELETE FROM pending_delete WHERE arxiv_id IN (SELECT unnest(?))", [ids])
            db.commit()
        except BaseException:
            db.rollback()
            raise
        batches += 1
        print(orjson.dumps({"stage":"publish-incremental" if published else "publish", "collection":target, "batch":batches, "indexed":len(present), "deleted":len(removed), "pending_publish":db.execute("SELECT count(*) FROM pending_publish").fetchone()[0], "pending_delete":db.execute("SELECT count(*) FROM pending_delete").fetchone()[0]}).decode(), flush=True)
    pending = db.execute("SELECT (SELECT count(*) FROM pending_publish)+(SELECT count(*) FROM pending_delete)").fetchone()[0]
    if not published and not pending:
        expected = db.execute("SELECT count(*) FROM arxiv").fetchone()[0]
        actual = (await qdrant.count(target, exact=True)).count
        if actual != expected:
            raise RuntimeError(f"collection count mismatch: expected {expected}, got {actual}")
        await switch_alias(qdrant, alias, target)
        db.execute("UPDATE import_state SET published=true")
    return database_status(db)

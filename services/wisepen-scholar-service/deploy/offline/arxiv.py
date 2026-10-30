"""流式导入 arXiv；来源事实、投影及字节游标在同一 DuckDB 事务提交。"""

from __future__ import annotations

import os
from email.utils import parsedate_to_datetime
from pathlib import Path

import duckdb
import orjson
from offline.embeddings import EMBEDDING_SCHEMA, reconcile_embeddings

from scholar.domain.models.identifiers import normalize_arxiv, normalize_doi


def open_database(
    path: str | Path,
    *,
    memory_limit: str | None = None,
    threads: int | str | None = None,
) -> duckdb.DuckDBPyConnection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "memory_limit": memory_limit or os.getenv("DUCKDB_MEMORY_LIMIT", "4GB"),
        "threads": str(threads or os.getenv("DUCKDB_THREADS", "4")),
    }
    db = duckdb.connect(str(path), config=config)
    db.execute("""
               CREATE TABLE IF NOT EXISTS arxiv
               (
                   arxiv_id
                   VARCHAR
                   PRIMARY
                   KEY,
                   doi
                   VARCHAR,
                   source
                   JSON,
                   document
                   JSON
               );
               CREATE INDEX IF NOT EXISTS idx_arxiv_doi ON arxiv(doi);
               CREATE TABLE IF NOT EXISTS pending_publish
               (
                   arxiv_id
                   VARCHAR
                   PRIMARY
                   KEY
               );
               CREATE TABLE IF NOT EXISTS pending_delete
               (
                   arxiv_id
                   VARCHAR
                   PRIMARY
                   KEY,
                   datestamp
                   VARCHAR
                   NOT
                   NULL
               );
               CREATE TABLE IF NOT EXISTS import_state
               (
                   singleton
                   BOOLEAN
                   PRIMARY
                   KEY
                   DEFAULT
                   true
                   CHECK
               (
                   singleton
               ),
                   source_path VARCHAR, source_size BIGINT, source_mtime_ns BIGINT,
                   byte_offset BIGINT, records BIGINT, complete BOOLEAN,
                   target_index VARCHAR, published BOOLEAN DEFAULT false
                   );
               """)
    db.execute(EMBEDDING_SCHEMA)
    return db


def import_arxiv(
    db: duckdb.DuckDBPyConnection,
    source: str | Path,
    *,
    batch_size: int = 5000,
    max_batches: int | None = None,
) -> dict:
    """中断后从已提交字节位置继续；替换过的输入必须使用新的数据库。"""
    source = Path(source).resolve()
    stat = source.stat()
    identity = (str(source), stat.st_size, stat.st_mtime_ns)
    state = db.execute(
        "SELECT source_path, source_size, source_mtime_ns, byte_offset, records, complete FROM import_state"
    ).fetchone()
    if state is None:
        db.execute(
            "INSERT INTO import_state(singleton,source_path,source_size,source_mtime_ns,byte_offset,records,complete) VALUES (true,?,?,?,0,0,false)",
            identity,
        )
        offset, count, complete = 0, 0, False
    else:
        if state[:3] != identity:
            raise ValueError(
                "input differs from checkpoint; use a new database for a new snapshot"
            )
        offset, count, complete = state[3:]
    batches = 0
    with source.open("rb") as stream:
        stream.seek(offset)
        while not complete and (max_batches is None or batches < max_batches):
            ids, dois, raw, documents = [], [], [], []
            for _ in range(batch_size):
                line = stream.readline()
                if not line:
                    complete = True
                    break
                record = orjson.loads(line)
                arxiv_id = normalize_arxiv(record["id"])
                # arXiv 历史快照的 DOI 字段可能包含说明或多个值；只有单个合法 DOI 参与精确查询。
                doi = None
                if record["doi"]:
                    try:
                        doi = normalize_doi(record["doi"])
                    except ValueError:
                        pass  # 原文仍在 source 中，不把无法规范化的文本当作身份。
                document = {
                    "arxiv_id": arxiv_id,
                    "raw_id": record["id"],
                    "doi": doi,
                    "title": " ".join(record["title"].split()),
                    "abstract": record["abstract"].strip(),
                    "authors": [
                        " ".join(
                            part for part in (author[1], author[0], *author[2:]) if part
                        ).strip()
                        for author in record["authors_parsed"]
                    ],
                    "categories": record["categories"].split(),
                    "submitted_date": parsedate_to_datetime(
                        record["versions"][0]["created"]
                    )
                    .date()
                    .isoformat(),
                    "updated_date": record["update_date"],
                    "versions": record["versions"],
                    "journal_ref": record["journal-ref"],
                    "comments": record["comments"],
                    "license": record["license"],
                    "url": f"https://arxiv.org/abs/{arxiv_id}",
                    "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                }
                ids.append(arxiv_id)
                dois.append(doi)
                raw.append(record)
                documents.append(document)
            offset, count = stream.tell(), count + len(ids)
            db.begin()
            try:
                if ids:
                    # JSON 批次只绑定一个标量，避免 Python 驱动逐元素探测可选数据框库。
                    payload = orjson.dumps(
                        [
                            {"arxiv_id": i, "doi": d, "source": s, "document": p}
                            for i, d, s, p in zip(
                            ids, dois, raw, documents, strict=True
                        )
                        ]
                    ).decode()
                    # 实际快照含重复 ID：按来源更新日期保留最新记录，同日以文件后出现的记录为准。
                    db.execute(
                        """INSERT OR REPLACE INTO arxiv
                        WITH incoming AS (
                            SELECT value->>'arxiv_id' AS arxiv_id, value->>'doi' AS doi,
                                value->'source' AS source, value->'document' AS document,
                                row_number() OVER (
                                    PARTITION BY value->>'arxiv_id'
                                    ORDER BY json_extract_string(value,'$.document.updated_date') DESC,
                                        key::BIGINT DESC
                                ) AS position
                            FROM json_each(?)
                        )
                        SELECT i.arxiv_id,i.doi,i.source,i.document FROM incoming i
                        LEFT JOIN arxiv a USING(arxiv_id)
                        WHERE i.position=1 AND (a.arxiv_id IS NULL OR
                            json_extract_string(i.document,'$.updated_date') >=
                            json_extract_string(a.document,'$.updated_date'))""",
                        [payload],
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO pending_publish SELECT value->>'arxiv_id' FROM json_each(?)",
                        [payload],
                    )
                if ids:
                    reconcile_embeddings(db, ids)
                db.execute(
                    "UPDATE import_state SET byte_offset=?,records=?,complete=?",
                    [offset, count, complete],
                )
                db.commit()
            except BaseException:
                db.rollback()
                raise
            batches += 1
            print(
                orjson.dumps(
                    {
                        "stage": "import",
                        "records": count,
                        "bytes": offset,
                        "complete": complete,
                    }
                ).decode(),
                flush=True,
            )
    return database_status(db)


def database_status(db: duckdb.DuckDBPyConnection) -> dict:
    row = db.execute(
        "SELECT byte_offset,records,complete,target_index,published FROM import_state"
    ).fetchone()
    return {
        "papers": db.execute("SELECT count(*) FROM arxiv").fetchone()[0],
        "pending": db.execute("SELECT count(*) FROM pending_publish").fetchone()[0],
        "import": None
        if row is None
        else dict(
            zip(
                ("byte_offset", "records", "complete", "target_index", "published"), row
            )
        ),
    }

"""协调 OAI 基础事实、S2 待补充记录、缓存失效及整轮发布游标。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from pathlib import Path
from xml.etree import ElementTree as ET

import duckdb
import httpx
import orjson
from offline.arxiv_oai import ArxivOaiHarvest, ArxivOaiRecord, INITIAL_FROM
from offline.embeddings import INPUT_HASH_SQL, reconcile_embeddings
from offline.s2_api import S2SyncClient, S2SyncPaper

from scholar.core.config.bootstrap_settings import SERVICE_ROOT

from scholar.domain.models.identifiers import (
    IdentifierKind,
    PaperIdentifier,
    normalize_arxiv,
    normalize_doi,
)
from scholar.domain.repositories.paper_lookup_cache import PaperLookupCache
from scholar.ml.specter import MODEL_SIGNATURE

_RAW_NS = "http://arxiv.org/OAI/arXivRaw/"


class _DiagnosticReason(StrEnum):
    INVALID_ARXIV_ID = "invalid_arxiv_id"
    ARXIV_ID_CONFLICT = "arxiv_id_conflict"
    INVALID_DOI = "invalid_doi"
    DOI_CONFLICT = "doi_conflict"
    ENRICHMENT_ARXIV_CONFLICT = "enrichment_arxiv_conflict"
    ENRICHMENT_DOI_CONFLICT = "enrichment_doi_conflict"
    ENRICHMENT_CORPUS_CONFLICT = "enrichment_corpus_conflict"


class _PendingS2Reason(StrEnum):
    NEW_PAPER = "new_paper"
    IDENTITY_CHANGED = "identity_changed"
    CORE_MISSING = "core_missing"
    UPDATED_PAPER = "updated_paper"


@dataclass
class _Projection:
    arxiv_id: str
    doi: str | None
    source: dict
    document: dict
    diagnostics: list[tuple[_DiagnosticReason, dict]]


def _create_sync_schema(db: duckdb.DuckDBPyConnection) -> None:
    db.execute("""
               CREATE TABLE IF NOT EXISTS arxiv_sync_state
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
                   last_response_date VARCHAR
                   );
               CREATE TABLE IF NOT EXISTS pending_s2
               (
                   arxiv_id
                   VARCHAR
                   PRIMARY
                   KEY,
                   reason
                   VARCHAR
                   NOT
                   NULL
               );
               CREATE TABLE IF NOT EXISTS pending_s2_core_missing
               (
                   arxiv_id VARCHAR PRIMARY KEY,
                   reason VARCHAR NOT NULL,
                   retry_count INTEGER DEFAULT 0,
                   created_at VARCHAR NOT NULL
               );
               CREATE TABLE IF NOT EXISTS pending_arxiv_cache_invalidation
               (
                   arxiv_id
                   VARCHAR
                   PRIMARY
                   KEY,
                   identifiers
                   JSON
                   NOT
                   NULL
               );
               CREATE TABLE IF NOT EXISTS arxiv_sync_diagnostics
               (
                   response_date
                   VARCHAR
                   NOT
                   NULL,
                   oai_identifier
                   VARCHAR
                   NOT
                   NULL,
                   arxiv_id
                   VARCHAR,
                   reason
                   VARCHAR
                   NOT
                   NULL,
                   detail
                   JSON
                   NOT
                   NULL,
                   PRIMARY
                   KEY
               (
                   response_date,
                   oai_identifier,
                   reason
               )
                   );
               """)


def _normalize_oai_identifier(identifier: str) -> str:
    prefix = "oai:arXiv.org:"
    if not identifier.startswith(prefix):
        raise ValueError("OAI identifier is not an arXiv identifier")
    return normalize_arxiv(identifier.removeprefix(prefix))


def _save_harvest_cache(path: Path, harvest: ArxivOaiHarvest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records_data = [
        (
            r.identifier,
            r.datestamp,
            r.deleted,
            ET.tostring(r.metadata, encoding="utf-8").decode("utf-8")
            if r.metadata is not None
            else None,
        )
        for r in harvest.records
    ]
    data = {
        "response_date": harvest.response_date,
        "records": records_data,
    }
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_bytes(orjson.dumps(data))
    tmp_path.replace(path)


def _load_harvest_cache(path: Path) -> ArxivOaiHarvest | None:
    if not path.exists():
        return None
    try:
        data = orjson.loads(path.read_bytes())
        records = tuple(
            ArxivOaiRecord(
                identifier,
                datestamp,
                deleted,
                ET.fromstring(meta_str) if meta_str is not None else None,
            )
            for identifier, datestamp, deleted, meta_str in data["records"]
        )
        return ArxivOaiHarvest(data["response_date"], records)
    except Exception:
        return None


def _project_record(record: ArxivOaiRecord, header_id: str) -> _Projection:
    metadata = record.metadata
    if metadata is None:
        raise ValueError("active OAI record is missing arXivRaw metadata")

    raw_id = metadata.findtext(f"{{{_RAW_NS}}}id")
    if raw_id is None:
        raise ValueError("arXivRaw metadata is missing its id")
    arxiv_id = normalize_arxiv(raw_id)
    diagnostics = []
    if arxiv_id != header_id:
        diagnostics.append(
            (
                _DiagnosticReason.ARXIV_ID_CONFLICT,
                {"header_arxiv_id": header_id, "metadata_arxiv_id": arxiv_id},
            )
        )
        return _Projection(arxiv_id, None, {}, {}, diagnostics)

    raw_doi = metadata.findtext(f"{{{_RAW_NS}}}doi")
    doi = None
    if raw_doi:
        try:
            doi = normalize_doi(raw_doi)
        except ValueError:
            diagnostics.append((_DiagnosticReason.INVALID_DOI, {"raw_doi": raw_doi}))

    versions = []
    for version in metadata.findall(f"{{{_RAW_NS}}}version"):
        created = version.findtext(f"{{{_RAW_NS}}}date")
        versions.append({"version": version.attrib["version"], "created": created})

    authors_text = metadata.findtext(f"{{{_RAW_NS}}}authors") or ""
    # arXivRaw 保存逗号分隔的扁平作者串，不使用 Kaggle 的 authors_parsed 结构。
    authors = [author.strip() for author in authors_text.split(",") if author.strip()]
    title = metadata.findtext(f"{{{_RAW_NS}}}title")
    abstract = metadata.findtext(f"{{{_RAW_NS}}}abstract")
    datestamp = date.fromisoformat(record.datestamp[:10]).isoformat()
    document = {
        "arxiv_id": arxiv_id,
        "raw_id": raw_id,
        "doi": doi,
        "title": None if title is None else " ".join(title.split()),
        "abstract": None if abstract is None else " ".join(abstract.split()),
        "authors": authors,
        "categories": (metadata.findtext(f"{{{_RAW_NS}}}categories") or "").split(),
        "submitted_date": None
        if not versions
        else parsedate_to_datetime(versions[0]["created"]).date().isoformat(),
        "updated_date": datestamp,
        "versions": versions,
        "journal_ref": metadata.findtext(f"{{{_RAW_NS}}}journal-ref"),
        "comments": metadata.findtext(f"{{{_RAW_NS}}}comments"),
        "license": metadata.findtext(f"{{{_RAW_NS}}}license"),
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
    }
    source = {
        "oai_identifier": record.identifier,
        "datestamp": record.datestamp,
        "metadata_xml": ET.tostring(metadata, encoding="unicode"),
    }
    return _Projection(arxiv_id, doi, source, document, diagnostics)


def _s2_metadata(document: dict) -> dict:
    return (document.get("metadata") or {}).get("s2") or {}


def _core_s2_missing(document: dict) -> bool:
    s2 = _s2_metadata(document)
    tldr = s2.get("tldr")
    return (
        s2.get("citation_count") is None
        or s2.get("influential_citation_count") is None
        or tldr is None
        or tldr.get("text") is None
    )


def _identity_aliases(
    arxiv_id: str, doi: str | None, document: dict
) -> list[PaperIdentifier]:
    aliases = [PaperIdentifier(IdentifierKind.ARXIV, arxiv_id)]
    if doi is not None:
        aliases.append(PaperIdentifier(IdentifierKind.DOI, doi))
    corpus_id = _s2_metadata(document).get("corpus_id")
    if corpus_id is not None:
        aliases.append(PaperIdentifier(IdentifierKind.CORPUS_ID, str(corpus_id)))
    return list(dict.fromkeys(aliases))


def _pending_invalidation_aliases(
    db: duckdb.DuckDBPyConnection,
) -> list[PaperIdentifier]:
    rows = db.execute("""
                      SELECT a.arxiv_id, a.doi, a.document
                      FROM arxiv a
                               JOIN (SELECT arxiv_id
                                     FROM pending_s2
                                     UNION
                                     SELECT arxiv_id
                                     FROM pending_publish) p USING (arxiv_id)
                      ORDER BY a.arxiv_id
                      """).fetchall()
    aliases = [
        identifier
        for arxiv_id, doi, raw_document in rows
        for identifier in _identity_aliases(arxiv_id, doi, orjson.loads(raw_document))
    ]
    aliases.extend(
        PaperIdentifier(IdentifierKind.ARXIV, arxiv_id)
        for (arxiv_id,) in db.execute(
            "SELECT arxiv_id FROM pending_delete ORDER BY arxiv_id"
        ).fetchall()
    )
    return list(dict.fromkeys(aliases))


def _record_arxiv_cache_invalidation(
    db: duckdb.DuckDBPyConnection,
    arxiv_id: str,
    identifiers: list[PaperIdentifier],
) -> None:
    """合并同篇论文的旧、新别名，保留发布后失效失败时的重放依据。"""
    row = db.execute(
        "SELECT identifiers FROM pending_arxiv_cache_invalidation WHERE arxiv_id=?",
        [arxiv_id],
    ).fetchone()
    if row is not None:
        identifiers = [
                          PaperIdentifier(IdentifierKind(kind), value)
                          for kind, value in orjson.loads(row[0])
                      ] + identifiers
    encoded = orjson.dumps(
        [
            [identifier.kind.value, identifier.value]
            for identifier in dict.fromkeys(identifiers)
        ]
    ).decode()
    db.execute(
        """INSERT OR REPLACE INTO pending_arxiv_cache_invalidation
        VALUES (?, ?::JSON)""",
        [arxiv_id, encoded],
    )


def _write_diagnostic(
    db: duckdb.DuckDBPyConnection,
    response_date: str,
    oai_identifier: str,
    arxiv_id: str | None,
    reason: _DiagnosticReason | str,
    detail: dict,
) -> None:
    reason_str = reason.value if hasattr(reason, "value") else str(reason)
    db.execute(
        """INSERT OR REPLACE INTO arxiv_sync_diagnostics
        VALUES (?, ?, ?, ?, ?::JSON)""",
        [
            response_date,
            oai_identifier,
            arxiv_id,
            reason_str,
            orjson.dumps(detail).decode(),
        ],
    )


def _apply_deletion(
    db: duckdb.DuckDBPyConnection,
    record: ArxivOaiRecord,
    arxiv_id: str,
    *,
    doi_lookup: dict[str, list[str]] | None = None,
) -> list[PaperIdentifier]:
    row = db.execute(
        "SELECT doi, document FROM arxiv WHERE arxiv_id=?", [arxiv_id]
    ).fetchone()
    old_doi = None if row is None else row[0]
    if doi_lookup is not None and old_doi and old_doi in doi_lookup:
        if arxiv_id in doi_lookup[old_doi]:
            doi_lookup[old_doi].remove(arxiv_id)
    aliases = (
        _identity_aliases(arxiv_id, row[0], orjson.loads(row[1]))
        if row is not None
        else [PaperIdentifier(IdentifierKind.ARXIV, arxiv_id)]
    )
    db.execute("DELETE FROM arxiv WHERE arxiv_id=?", [arxiv_id])
    db.execute("DELETE FROM paper_embeddings WHERE arxiv_id=?", [arxiv_id])
    db.execute("DELETE FROM pending_embeddings WHERE arxiv_id=?", [arxiv_id])
    db.execute("DELETE FROM pending_publish WHERE arxiv_id=?", [arxiv_id])
    db.execute("DELETE FROM pending_s2 WHERE arxiv_id=?", [arxiv_id])
    db.execute("DELETE FROM pending_s2_core_missing WHERE arxiv_id=?", [arxiv_id])
    db.execute(
        """INSERT OR REPLACE INTO pending_delete(arxiv_id, datestamp)
        VALUES (?, ?)""",
        [arxiv_id, record.datestamp],
    )
    return aliases


def _apply_projection(
    db: duckdb.DuckDBPyConnection,
    projection: _Projection,
    record: ArxivOaiRecord,
    response_date: str,
    *,
    doi_lookup: dict[str, list[str]] | None = None,
) -> tuple[bool, list[PaperIdentifier]]:
    for reason, detail in projection.diagnostics:
        _write_diagnostic(
            db,
            response_date,
            record.identifier,
            projection.arxiv_id,
            reason,
            detail,
        )
    if projection.diagnostics and any(
        reason == _DiagnosticReason.ARXIV_ID_CONFLICT
        for reason, _ in projection.diagnostics
    ):
        return False, []

    row = db.execute(
        "SELECT doi, document FROM arxiv WHERE arxiv_id=?", [projection.arxiv_id]
    ).fetchone()
    old_document = {} if row is None else orjson.loads(row[1])
    old_doi = None if row is None else row[0]
    identity_changed = row is not None and old_doi != projection.doi
    document = projection.document.copy()
    metadata = dict(old_document.get("metadata") or {})
    if identity_changed:
        metadata.pop("s2", None)
    if metadata:
        document["metadata"] = metadata

    other_doi_ids = []
    if projection.doi is not None:
        if doi_lookup is not None and projection.doi in doi_lookup:
            other_doi_ids = sorted(
                aid
                for aid in doi_lookup[projection.doi]
                if aid != projection.arxiv_id
            )
        else:
            other_doi_ids = [
                item[0]
                for item in db.execute(
                    "SELECT arxiv_id FROM arxiv WHERE doi=? AND arxiv_id<>? ORDER BY arxiv_id",
                    [projection.doi, projection.arxiv_id],
                ).fetchall()
            ]
            if doi_lookup is not None:
                doi_lookup[projection.doi] = list(other_doi_ids)
    if other_doi_ids:
        _write_diagnostic(
            db,
            response_date,
            record.identifier,
            projection.arxiv_id,
            _DiagnosticReason.DOI_CONFLICT,
            {"doi": projection.doi, "other_arxiv_ids": other_doi_ids},
        )

    if doi_lookup is not None:
        if identity_changed and old_doi and old_doi in doi_lookup:
            if projection.arxiv_id in doi_lookup[old_doi]:
                doi_lookup[old_doi].remove(projection.arxiv_id)
        if projection.doi is not None and projection.arxiv_id not in doi_lookup[projection.doi]:
            doi_lookup[projection.doi].append(projection.arxiv_id)

    old_aliases = (
        _identity_aliases(projection.arxiv_id, old_doi, old_document)
        if row is not None
        else []
    )
    db.execute(
        """INSERT OR REPLACE INTO arxiv(arxiv_id, doi, source, document)
        VALUES (?, ?, ?::JSON, ?::JSON)""",
        [
            projection.arxiv_id,
            projection.doi,
            orjson.dumps(projection.source).decode(),
            orjson.dumps(document).decode(),
        ],
    )
    reconcile_embeddings(db, [projection.arxiv_id])
    db.execute("DELETE FROM pending_delete WHERE arxiv_id=?", [projection.arxiv_id])
    if document != old_document:
        db.execute(
            "INSERT OR IGNORE INTO pending_publish(arxiv_id) VALUES (?)",
            [projection.arxiv_id],
        )

    needs_vector = (
        db.execute(
            "SELECT 1 FROM pending_embeddings WHERE arxiv_id=?", [projection.arxiv_id]
        ).fetchone()
        is not None
    )
    if document != old_document or _core_s2_missing(document) or needs_vector:
        reason = (
            _PendingS2Reason.NEW_PAPER
            if row is None
            else _PendingS2Reason.IDENTITY_CHANGED
            if identity_changed
            else _PendingS2Reason.UPDATED_PAPER
        )
        db.execute(
            """INSERT OR REPLACE INTO pending_s2(arxiv_id, reason)
            VALUES (?, ?)""",
            [projection.arxiv_id, reason.value],
        )
    # 已排队的刷新由 S2 成功提交后移除；OAI 重放不能因旧信号齐全而丢弃待办。

    aliases = old_aliases + _identity_aliases(
        projection.arxiv_id, projection.doi, document
    )
    return document != old_document, list(dict.fromkeys(aliases))


def _s2_lookup_conflicts(
    db: duckdb.DuckDBPyConnection,
    arxiv_id: str,
    doi: str | None,
    response: S2SyncPaper,
    duplicate_corpus_ids: set[str],
    *,
    doi_lookup: dict[str, list[str]] | None = None,
    corpus_lookup: dict[str, list[str]] | None = None,
) -> list[tuple[_DiagnosticReason, dict]]:
    s2 = response.metadata["s2"]
    response_arxiv_ids = {
        identifier.value
        for identifier in response.identifiers
        if identifier.kind == IdentifierKind.ARXIV
    }
    response_dois = {
        identifier.value
        for identifier in response.identifiers
        if identifier.kind == IdentifierKind.DOI
    }
    corpus_id = s2.get("corpus_id")
    response_corpus_ids = {
        identifier.value
        for identifier in response.identifiers
        if identifier.kind == IdentifierKind.CORPUS_ID
    }
    conflicts = []
    if any(identifier != arxiv_id for identifier in response_arxiv_ids):
        conflicts.append(
            (
                _DiagnosticReason.ENRICHMENT_ARXIV_CONFLICT,
                {
                    "expected_arxiv_id": arxiv_id,
                    "response_arxiv_ids": sorted(response_arxiv_ids),
                },
            )
        )
    if len(response_dois) > 1 or (
        doi is not None and any(value != doi for value in response_dois)
    ):
        conflicts.append(
            (
                _DiagnosticReason.ENRICHMENT_DOI_CONFLICT,
                {"expected_doi": doi, "response_dois": sorted(response_dois)},
            )
        )
    for response_doi in response_dois:
        if doi_lookup is not None:
            owners = [
                (aid,)
                for aid in sorted(doi_lookup.get(response_doi, []))
                if aid != arxiv_id
            ]
        else:
            owners = db.execute(
                "SELECT arxiv_id FROM arxiv WHERE doi=? AND arxiv_id<>? ORDER BY arxiv_id",
                [response_doi, arxiv_id],
            ).fetchall()
        if owners:
            conflicts.append(
                (
                    _DiagnosticReason.ENRICHMENT_DOI_CONFLICT,
                    {
                        "response_doi": response_doi,
                        "other_arxiv_ids": [x[0] for x in owners],
                    },
                )
            )
    if response_corpus_ids and response_corpus_ids != {corpus_id}:
        conflicts.append(
            (
                _DiagnosticReason.ENRICHMENT_CORPUS_CONFLICT,
                {
                    "metadata_corpus_id": corpus_id,
                    "response_corpus_ids": sorted(response_corpus_ids),
                },
            )
        )
    if corpus_id is not None:
        corpus_id = str(corpus_id)
        if corpus_id in duplicate_corpus_ids:
            conflicts.append(
                (
                    _DiagnosticReason.ENRICHMENT_CORPUS_CONFLICT,
                    {"corpus_id": corpus_id, "reason": "duplicate_in_batch"},
                )
            )
        if corpus_lookup is not None:
            owners = [
                (aid,)
                for aid in sorted(corpus_lookup.get(corpus_id, []))
                if aid != arxiv_id
            ]
        else:
            owners = db.execute(
                """SELECT arxiv_id
                   FROM arxiv
                   WHERE arxiv_id <> ?
                     AND json_extract_string(document, '$.metadata.s2.corpus_id') = ?
                   ORDER BY arxiv_id""",
                [arxiv_id, corpus_id],
            ).fetchall()
        if owners:
            conflicts.append(
                (
                    _DiagnosticReason.ENRICHMENT_CORPUS_CONFLICT,
                    {"corpus_id": corpus_id, "other_arxiv_ids": [x[0] for x in owners]},
                )
            )
    return conflicts


async def _enrich_pending(
    db: duckdb.DuckDBPyConnection,
    client: S2SyncClient,
    *,
    batch_size: int,
    response_date: str,
) -> tuple[int, int, list[PaperIdentifier]]:
    rows = db.execute("""
                      SELECT a.arxiv_id, a.doi, a.document
                      FROM pending_s2 p
                               JOIN arxiv a USING (arxiv_id)
                      ORDER BY a.arxiv_id
                      """).fetchall()
    pending = [
        (arxiv_id, doi, orjson.loads(document)) for arxiv_id, doi, document in rows
    ]

    total_pending = len(pending)
    if total_pending:
        print(
            f"[S2] 发现 {total_pending} 篇待补充论文 (引用/TLDR/768维向量)，开始请求 Semantic Scholar API (每批 {batch_size} 篇)...",
            flush=True,
        )
    else:
        print("[S2] 当前待补充队列为空，无需请求 S2 API", flush=True)

    requested = 0
    enriched = 0
    invalidations = []
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset: offset + batch_size]
        identifiers = [PaperIdentifier(IdentifierKind.ARXIV, item[0]) for item in batch]

        max_retries = 5
        synced = None
        for attempt in range(max_retries):
            try:
                synced = await client.lookup(identifiers)
                break
            except Exception as err:
                err_type = type(err).__name__
                err_detail = str(err).strip() or repr(err)
                is_429 = "429" in err_detail or "too many requests" in err_detail.lower()
                wait_seconds = min(3.0 * (2 ** attempt), 30.0) if is_429 else min(2.0 * (attempt + 1), 10.0)
                if attempt < max_retries - 1:
                    print(
                        f"[S2] 批次 {offset}-{offset + len(batch)} 遇到异常 ({err_type}: {err_detail})，等待 {wait_seconds:.1f} 秒后重试 (第 {attempt + 1}/{max_retries} 次)...",
                        flush=True,
                    )
                    await asyncio.sleep(wait_seconds)
                    continue
                else:
                    print(
                        f"[S2] 批次 {offset}-{offset + len(batch)} 重试耗尽失败 ({err_type}: {err_detail})，跳过该批（保留在待办），继续后续批次...",
                        flush=True,
                    )
                    break

        if synced is None:
            continue

        requested += len(identifiers)
        corpus_owners: dict[str, set[str]] = {}
        for (arxiv_id, _, _), response in zip(batch, synced, strict=True):
            if (
                response is not None
                and response.metadata["s2"].get("corpus_id") is not None
            ):
                corpus_owners.setdefault(
                    str(response.metadata["s2"]["corpus_id"]), set()
                ).add(arxiv_id)
        duplicate_corpus_ids = {
            corpus_id for corpus_id, owners in corpus_owners.items() if len(owners) > 1
        }

        batch_dois = list({
            identifier.value
            for res in synced
            if res is not None
            for identifier in res.identifiers
            if identifier.kind == IdentifierKind.DOI
        })
        doi_lookup = defaultdict(list)
        if batch_dois:
            for d, aid in db.execute(
                "SELECT doi, arxiv_id FROM arxiv WHERE doi IN (SELECT unnest(?))",
                [batch_dois],
            ).fetchall():
                doi_lookup[d].append(aid)

        batch_corpus_ids = list({
            str(res.metadata["s2"]["corpus_id"])
            for res in synced
            if res is not None and res.metadata.get("s2", {}).get("corpus_id") is not None
        })
        corpus_lookup = defaultdict(list)
        if batch_corpus_ids:
            for cid, aid in db.execute(
                """SELECT json_extract_string(document, '$.metadata.s2.corpus_id') as cid, arxiv_id
                   FROM arxiv
                   WHERE json_extract_string(document, '$.metadata.s2.corpus_id') IN (SELECT unnest(?))
                   ORDER BY arxiv_id""",
                [batch_corpus_ids],
            ).fetchall():
                corpus_lookup[cid].append(aid)

        for (arxiv_id, doi, old_document), sync_result in zip(
            batch, synced, strict=True
        ):
            if sync_result is None:
                db.execute("DELETE FROM pending_s2 WHERE arxiv_id=?", [arxiv_id])
                db.execute(
                    """INSERT OR REPLACE INTO pending_s2_core_missing(arxiv_id, reason, retry_count, created_at)
                    VALUES (?, 'not_found', COALESCE((SELECT retry_count FROM pending_s2_core_missing WHERE arxiv_id=?), 0), ?)""",
                    [arxiv_id, arxiv_id, datetime.now(UTC).isoformat()],
                )
                continue
            conflicts = _s2_lookup_conflicts(
                db,
                arxiv_id,
                doi,
                sync_result,
                duplicate_corpus_ids,
                doi_lookup=doi_lookup,
                corpus_lookup=corpus_lookup,
            )
            if conflicts:
                db.begin()
                try:
                    for reason, detail in conflicts:
                        _write_diagnostic(
                            db,
                            response_date,
                            f"oai:arXiv.org:{arxiv_id}",
                            arxiv_id,
                            reason,
                            detail,
                        )
                    db.execute("DELETE FROM pending_s2 WHERE arxiv_id=?", [arxiv_id])
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
                continue

            metadata = dict(old_document.get("metadata") or {})
            metadata.update(sync_result.metadata)
            document = {**old_document, "metadata": metadata}
            db.begin()
            try:
                db.execute(
                    "UPDATE arxiv SET document=?::JSON WHERE arxiv_id=?",
                    [orjson.dumps(document).decode(), arxiv_id],
                )
                if document != old_document:
                    db.execute(
                        "INSERT OR IGNORE INTO pending_publish(arxiv_id) VALUES (?)",
                        [arxiv_id],
                    )
                # 向量和元数据同事务提交；未找到向量时保留待办，下一轮调用 S2 重试，不启动 GPU。
                if sync_result.vector is not None:
                    db.execute(
                        f"""INSERT OR REPLACE INTO paper_embeddings
                        SELECT a.arxiv_id, ?, {INPUT_HASH_SQL}, ?::FLOAT[768]
                        FROM arxiv a WHERE a.arxiv_id=?""",
                        [MODEL_SIGNATURE, sync_result.vector, arxiv_id],
                    )
                    db.execute(
                        "DELETE FROM pending_embeddings WHERE arxiv_id=?", [arxiv_id]
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO pending_publish VALUES (?)", [arxiv_id]
                    )
                needs_vector = (
                    db.execute(
                        "SELECT 1 FROM pending_embeddings WHERE arxiv_id=?", [arxiv_id]
                    ).fetchone()
                    is not None
                )
                # 只要当前批次处理过即从 current pending_s2 移除，不占日常同步名额
                db.execute("DELETE FROM pending_s2 WHERE arxiv_id=?", [arxiv_id])
                if _core_s2_missing(document) or needs_vector:
                    # 缺少核心元数据 (TLDR/引用数) 或 768 维向量，放入 core_missing 独立队列
                    db.execute(
                        """INSERT OR REPLACE INTO pending_s2_core_missing(arxiv_id, reason, retry_count, created_at)
                        VALUES (?, 'core_missing', COALESCE((SELECT retry_count FROM pending_s2_core_missing WHERE arxiv_id=?), 0), ?)""",
                        [arxiv_id, arxiv_id, datetime.now(UTC).isoformat()],
                    )
                else:
                    db.execute("DELETE FROM pending_s2_core_missing WHERE arxiv_id=?", [arxiv_id])
                if document != old_document or sync_result.vector is not None:
                    # S2 待办可在空 OAI 响应中单独发布，仍需留下发布后的缓存失效依据。
                    _record_arxiv_cache_invalidation(
                        db,
                        arxiv_id,
                        _identity_aliases(arxiv_id, doi, old_document)
                        + _identity_aliases(arxiv_id, doi, document),
                    )
                db.commit()
            except BaseException:
                db.rollback()
                raise

            if document != old_document:
                invalidations.extend(
                    _identity_aliases(arxiv_id, doi, old_document)
                    + _identity_aliases(arxiv_id, doi, document)
                )
                enriched += 1

        batch_pct = (requested / total_pending * 100) if total_pending else 100
        print(
            f"[S2] 富化进度: {requested}/{total_pending} ({batch_pct:.1f}%) | 当前已成功补全: {enriched} 篇",
            flush=True,
        )

    if total_pending:
        print(
            f"[S2] 富化处理完毕: 共请求 {requested} 篇，成功补全 {enriched} 篇 (暂未收录的论文将保留在 pending_s2 待办队列)",
            flush=True,
        )

    return requested, enriched, list(dict.fromkeys(invalidations))


def _apply_chunk(
    db: duckdb.DuckDBPyConnection,
    chunk: list[ArxivOaiRecord],
    response_date: str,
    *,
    doi_lookup: dict[str, list[str]],
) -> tuple[int, int, list[PaperIdentifier]]:
    """极速批量入库：主键点查保留旧 S2 元数据与别名，批量写入各表，绝不执行全表扫描。"""
    active_payload = []
    chunk_invalidations = []
    deleted_count = 0
    updated_count = 0

    for record in chunk:
        try:
            aid = _normalize_oai_identifier(record.identifier)
        except ValueError as error:
            _write_diagnostic(
                db,
                response_date,
                record.identifier,
                None,
                _DiagnosticReason.INVALID_ARXIV_ID,
                {"message": str(error), "datestamp": record.datestamp},
            )
            continue

        if record.deleted:
            aliases = _apply_deletion(db, record, aid, doi_lookup=doi_lookup)
            _record_arxiv_cache_invalidation(db, aid, aliases)
            chunk_invalidations.extend(aliases)
            deleted_count += 1
        else:
            proj = _project_record(record, aid)
            for reason, detail in proj.diagnostics:
                _write_diagnostic(
                    db,
                    response_date,
                    record.identifier,
                    proj.arxiv_id,
                    reason,
                    detail,
                )
            if any(
                reason == _DiagnosticReason.ARXIV_ID_CONFLICT
                for reason, _ in proj.diagnostics
            ):
                continue

            # 使用主键点查（0.001s/条），查出旧文档并保留已有 S2 元数据与计算别名
            row = db.execute("SELECT doi, document FROM arxiv WHERE arxiv_id=?", [aid]).fetchone()
            old_doi = row[0] if row else None
            old_doc = orjson.loads(row[1]) if row else {}
            identity_changed = row is not None and old_doi != proj.doi

            doc = proj.document.copy()
            meta = dict(old_doc.get("metadata") or {})
            if identity_changed:
                meta.pop("s2", None)
            if meta:
                doc["metadata"] = meta

            old_aliases = (
                _identity_aliases(aid, old_doi, old_doc)
                if row is not None
                else []
            )
            aliases = list(dict.fromkeys(old_aliases + _identity_aliases(aid, proj.doi, doc)))
            chunk_invalidations.extend(aliases)
            _record_arxiv_cache_invalidation(db, aid, aliases)

            is_changed = (doc != old_doc)
            if is_changed:
                updated_count += 1

            active_payload.append({
                "arxiv_id": aid,
                "doi": proj.doi,
                "source": proj.source,
                "document": doc,
                "row": row,
                "identity_changed": identity_changed,
                "is_changed": is_changed,
            })

    if not active_payload:
        return 0, deleted_count, chunk_invalidations

    active_ids = [p["arxiv_id"] for p in active_payload]

    # 批量直接写入 DuckDB (耗时 < 0.5s)
    payload_str = orjson.dumps([
        {"arxiv_id": p["arxiv_id"], "doi": p["doi"], "source": p["source"], "document": p["document"]}
        for p in active_payload
    ]).decode()

    db.execute("""
        INSERT OR REPLACE INTO arxiv
        SELECT value->>'arxiv_id', value->>'doi', value->'source', value->'document'
        FROM json_each(?)
    """, [payload_str])

    # 待发布队列
    changed_ids = [p["arxiv_id"] for p in active_payload if p["is_changed"]]
    if changed_ids:
        db.executemany("INSERT OR IGNORE INTO pending_publish(arxiv_id) VALUES (?)", [[i] for i in changed_ids])

    # 向量队列与指纹检查
    reconcile_embeddings(db, active_ids)

    # 待 S2 富化队列：检查变更、核心元数据缺失或需要向量
    needs_vector_set = {
        r[0]
        for r in db.execute(
            "SELECT arxiv_id FROM pending_embeddings WHERE arxiv_id IN (SELECT unnest(?))",
            [active_ids],
        ).fetchall()
    }
    s2_rows = []
    for p in active_payload:
        aid = p["arxiv_id"]
        if p["is_changed"] or _core_s2_missing(p["document"]) or (aid in needs_vector_set):
            reason = (
                _PendingS2Reason.NEW_PAPER
                if p["row"] is None
                else _PendingS2Reason.IDENTITY_CHANGED
                if p["identity_changed"]
                else _PendingS2Reason.UPDATED_PAPER
            )
            s2_rows.append((aid, reason.value))
    if s2_rows:
        db.executemany("INSERT OR REPLACE INTO pending_s2(arxiv_id, reason) VALUES (?, ?)", s2_rows)

    # 清理复活的删除标记
    db.executemany("DELETE FROM pending_delete WHERE arxiv_id=?", [[i] for i in active_ids])

    return updated_count, deleted_count, chunk_invalidations


async def sync_arxiv(
    db: duckdb.DuckDBPyConnection,
    harvester,
    client: S2SyncClient,
    *,
    from_date: str | None = None,
    batch_size: int = 500,
    publish: Callable[[], Awaitable[object]],
    cache: PaperLookupCache,
    arxiv_cache: PaperLookupCache,
    cache_harvest: bool = False,
) -> dict:
    """同步 OAI 与 S2 投影，并在两类缓存处理成功后确认响应日期。"""
    _create_sync_schema(db)
    state = db.execute(
        "SELECT last_response_date FROM arxiv_sync_state WHERE singleton=true"
    ).fetchone()
    effective_from = (
        from_date
        if from_date is not None
        else state[0].partition("T")[0]
        if state is not None and state[0] is not None
        else INITIAL_FROM
    )
    if from_date is None and (state is None or state[0] is None) and cache_harvest:
        existing_caches = sorted((SERVICE_ROOT / ".runtime").glob("oai_harvest_cache_*.json"))
        if existing_caches:
            cached_date = existing_caches[-1].stem.partition("oai_harvest_cache_")[2]
            if cached_date:
                effective_from = cached_date
    print(f"[Sync] 准备执行每日同步 (起始日期: {effective_from})...", flush=True)

    cache_path = (
        SERVICE_ROOT / ".runtime" / f"oai_harvest_cache_{effective_from}.json"
        if cache_harvest
        else None
    )
    harvest = _load_harvest_cache(cache_path) if cache_path is not None else None
    if harvest is not None:
        print(
            f"[OAI] 发现本地增量缓存 ({cache_path.name})，直接载入已拉取的 {len(harvest.records)} 篇变动记录 (跳过网络请求)！",
            flush=True,
        )
    else:
        harvest = await harvester.list_records(from_date=effective_from)
        if cache_path is not None:
            _save_harvest_cache(cache_path, harvest)
            print(
                f"[OAI] 变动记录已持久化到本地缓存 ({cache_path.name})",
                flush=True,
            )

    total_records = len(harvest.records)
    if total_records:
        print(f"[Local] 正在将 {total_records} 篇 OAI 变动应用到本地数据库...", flush=True)
    else:
        print("[Local] 本次 OAI 无需写入变动", flush=True)

    doi_lookup: dict[str, list[str]] = {}
    invalidations = []
    updated = 0
    deleted = 0
    chunk_size = 500

    already_ingested = False
    if total_records and cache_harvest:
        sample_records = [r for r in harvest.records if not r.deleted]
        if sample_records:
            samples = [
                _normalize_oai_identifier(sample_records[0].identifier),
                _normalize_oai_identifier(sample_records[len(sample_records) // 2].identifier),
                _normalize_oai_identifier(sample_records[-1].identifier),
            ]
            in_db_count = db.execute(
                "SELECT count(*) FROM arxiv WHERE arxiv_id IN (SELECT unnest(?))",
                [samples],
            ).fetchone()[0]
            pending_pub_count = db.execute("SELECT count(*) FROM pending_publish").fetchone()[0]
            pending_s2_count = db.execute("SELECT count(*) FROM pending_s2").fetchone()[0]
            if in_db_count == len(samples) and (pending_pub_count > 0 or pending_s2_count > 0):
                already_ingested = True

    if already_ingested:
        print(
            f"[Local] 检测到本次 {total_records} 篇 OAI 变动已在本地 DuckDB 中，直接复用入库产物 (跳过重复写入)！",
            flush=True,
        )
    else:
        for chunk_start in range(0, total_records, chunk_size):
            chunk = harvest.records[chunk_start : chunk_start + chunk_size]
            db.begin()
            try:
                chunk_updated, chunk_deleted, chunk_invalidations = _apply_chunk(
                    db, chunk, harvest.response_date, doi_lookup=doi_lookup
                )
                updated += chunk_updated
                deleted += chunk_deleted
                invalidations.extend(chunk_invalidations)
                db.commit()
            except BaseException:
                db.rollback()
                raise

            processed = min(chunk_start + len(chunk), total_records)
            pct = (processed / total_records) * 100
            if total_records > chunk_size:
                print(
                    f"[Local] 本地数据库入库进度: {processed}/{total_records} ({pct:.1f}%)...",
                    flush=True,
                )

        if total_records:
            print(
                f"[Local] 本地数据库更新完毕: 新增/修改 {updated} 篇，标记删除 {deleted} 篇",
                flush=True,
            )

    # 待办记录会在外部操作完成前保留，重放可再次清理部分写入后的缓存。
    invalidations.extend(_pending_invalidation_aliases(db))
    if invalidations:
        await cache.invalidate(list(dict.fromkeys(invalidations)))

    requested, enriched, s2_invalidations = await _enrich_pending(
        db, client, batch_size=batch_size, response_date=harvest.response_date
    )
    if s2_invalidations:
        await cache.invalidate(s2_invalidations)

    pending_publish = db.execute("SELECT count(*) FROM pending_publish").fetchone()[0]
    pending_delete = db.execute("SELECT count(*) FROM pending_delete").fetchone()[0]
    published = bool(pending_publish or pending_delete)
    if published:
        print(
            f"[Publish] 正在发布到 Elasticsearch 与 Qdrant (待发布: {pending_publish} 篇，待删除: {pending_delete} 篇)...",
            flush=True,
        )
        await publish()
        if (
            db.execute("SELECT count(*) FROM pending_publish").fetchone()[0]
            or db.execute("SELECT count(*) FROM pending_delete").fetchone()[0]
        ):
            raise RuntimeError("arXiv publication returned with pending records")
        print("[Publish] 增量数据已发布成功到 Elasticsearch 与 Qdrant", flush=True)

    pending_aliases = db.execute(
        "SELECT identifiers FROM pending_arxiv_cache_invalidation ORDER BY arxiv_id"
    ).fetchall()
    arxiv_cache_aliases = list(
        dict.fromkeys(
            PaperIdentifier(IdentifierKind(kind), value)
            for (raw_identifiers,) in pending_aliases
            for kind, value in orjson.loads(raw_identifiers)
        )
    )
    if arxiv_cache_aliases:
        # pending_publish 会由发布器清理；此 journal 需等缓存失效成功后再清理，保证失败可重放。
        print(
            f"[Cache] 正在精确失效 {len(arxiv_cache_aliases)} 项 Redis 查询缓存...",
            flush=True,
        )
        await arxiv_cache.invalidate(arxiv_cache_aliases)
    db.begin()
    try:
        db.execute("DELETE FROM pending_arxiv_cache_invalidation")
        db.execute(
            """INSERT OR REPLACE INTO arxiv_sync_state(singleton, last_response_date)
            VALUES (true, ?)""",
            [harvest.response_date],
        )
        db.commit()
        print(f"[Sync] 同步游标已推进至 {harvest.response_date}", flush=True)
    except BaseException:
        db.rollback()
        raise

    diagnostics = db.execute(
        "SELECT count(*) FROM arxiv_sync_diagnostics WHERE response_date=?",
        [harvest.response_date],
    ).fetchone()[0]
    if cache_path is not None and cache_path.exists():
        cache_path.unlink(missing_ok=True)
    print(
        f"[Done] 每日同步完成！本次新增/更新: {updated} 篇，删除: {deleted} 篇，S2 富化: {enriched} 篇 (剩余待办 S2: {db.execute('SELECT count(*) FROM pending_s2').fetchone()[0]} 篇)",
        flush=True,
    )
    return {
        "from_date": effective_from,
        "response_date": harvest.response_date,
        "harvested": len(harvest.records),
        "updated": updated,
        "deleted": deleted,
        "s2_requested": requested,
        "s2_enriched": enriched,
        "pending_s2": db.execute("SELECT count(*) FROM pending_s2").fetchone()[0],
        "pending_publish": db.execute(
            "SELECT count(*) FROM pending_publish"
        ).fetchone()[0],
        "pending_delete": db.execute("SELECT count(*) FROM pending_delete").fetchone()[
            0
        ],
        "diagnostics": diagnostics,
        "published": published,
    }



async def recover_s2_missing(
    db: duckdb.DuckDBPyConnection,
    client: S2SyncClient,
    *,
    batch_size: int = 100,
    max_batches: int | None = None,
    publish: Callable[[], Awaitable[object]] | None = None,
    cache: PaperLookupCache | None = None,
) -> dict:
    """单独跑 core_missing 队列：对之前未收录或缺失核心信号（引用/TLDR/向量）的论文重试补全，不阻塞日常 sync。"""
    _create_sync_schema(db)
    rows = db.execute("""
        SELECT a.arxiv_id, a.doi, a.document, p.retry_count
        FROM pending_s2_core_missing p
        JOIN arxiv a USING (arxiv_id)
        ORDER BY p.retry_count ASC, a.arxiv_id ASC
    """).fetchall()

    total_missing = len(rows)
    print(f"[Recover] 发现 core_missing 待恢复队列共有 {total_missing} 篇论文 (按重试次数排序)...", flush=True)
    if not total_missing:
        return {"total": 0, "recovered": 0, "remaining": 0}

    recovered = 0
    requested = 0
    batches_done = 0
    invalidations = []

    for offset in range(0, total_missing, batch_size):
        if max_batches is not None and batches_done >= max_batches:
            print(f"[Recover] 已达到指定的批次上限 ({max_batches} 批)，暂停本轮恢复", flush=True)
            break

        batch = rows[offset: offset + batch_size]
        identifiers = [PaperIdentifier(IdentifierKind.ARXIV, item[0]) for item in batch]

        max_retries = 5
        synced = None
        for attempt in range(max_retries):
            try:
                synced = await client.lookup(identifiers)
                break
            except Exception as err:
                err_type = type(err).__name__
                err_detail = str(err).strip() or repr(err)
                is_429 = "429" in err_detail or "too many requests" in err_detail.lower()
                wait_seconds = min(3.0 * (2 ** attempt), 30.0) if is_429 else min(2.0 * (attempt + 1), 10.0)
                if attempt < max_retries - 1:
                    print(
                        f"[Recover] 批次遇到异常 ({err_type}: {err_detail})，等待 {wait_seconds:.1f} 秒后重试 (第 {attempt + 1}/{max_retries} 次)...",
                        flush=True,
                    )
                    await asyncio.sleep(wait_seconds)
                    continue
                else:
                    print(
                        f"[Recover] 批次重试耗尽失败 ({err_type}: {err_detail})，跳过该批，继续后续批次...",
                        flush=True,
                    )
                    break

        if synced is None:
            continue

        requested += len(identifiers)
        batches_done += 1

        for (arxiv_id, doi, raw_document, retry_count), sync_result in zip(batch, synced, strict=True):
            old_document = orjson.loads(raw_document)
            if sync_result is None:
                # 依然未收录：保留在队列并递增重试计数
                db.execute("UPDATE pending_s2_core_missing SET retry_count = retry_count + 1 WHERE arxiv_id=?", [arxiv_id])
                continue

            metadata = dict(old_document.get("metadata") or {})
            metadata.update(sync_result.metadata)
            document = {**old_document, "metadata": metadata}

            db.begin()
            try:
                db.execute(
                    "UPDATE arxiv SET document=?::JSON WHERE arxiv_id=?",
                    [orjson.dumps(document).decode(), arxiv_id],
                )
                if sync_result.vector is not None:
                    db.execute(
                        f"""INSERT OR REPLACE INTO paper_embeddings
                        SELECT a.arxiv_id, ?, {INPUT_HASH_SQL}, ?::FLOAT[768]
                        FROM arxiv a WHERE a.arxiv_id=?""",
                        [MODEL_SIGNATURE, sync_result.vector, arxiv_id],
                    )
                    db.execute("DELETE FROM pending_embeddings WHERE arxiv_id=?", [arxiv_id])
                    db.execute("INSERT OR IGNORE INTO pending_publish VALUES (?)", [arxiv_id])

                needs_vector = (
                    db.execute(
                        "SELECT 1 FROM pending_embeddings WHERE arxiv_id=?", [arxiv_id]
                    ).fetchone()
                    is not None
                )

                if not _core_s2_missing(document) and not needs_vector:
                    # 核心数据完全补齐，彻底出队！
                    db.execute("DELETE FROM pending_s2_core_missing WHERE arxiv_id=?", [arxiv_id])
                    recovered += 1
                else:
                    # 依然缺少部分核心数据，继续保留在队列并递增 retry_count
                    db.execute("UPDATE pending_s2_core_missing SET retry_count = retry_count + 1 WHERE arxiv_id=?", [arxiv_id])

                if document != old_document:
                    db.execute("INSERT OR IGNORE INTO pending_publish(arxiv_id) VALUES (?)", [arxiv_id])
                    _record_arxiv_cache_invalidation(
                        db,
                        arxiv_id,
                        _identity_aliases(arxiv_id, doi, old_document)
                        + _identity_aliases(arxiv_id, doi, document),
                    )
                    invalidations.extend(
                        _identity_aliases(arxiv_id, doi, old_document)
                        + _identity_aliases(arxiv_id, doi, document)
                    )
                db.commit()
            except BaseException:
                db.rollback()
                raise

        pct = (requested / total_missing * 100) if total_missing else 100
        print(f"[Recover] 恢复进度: {requested}/{total_missing} ({pct:.1f}%) | 本次成功补齐核心数据: {recovered} 篇", flush=True)

    if invalidations and cache is not None:
        await cache.invalidate(list(dict.fromkeys(invalidations)))

    remaining = db.execute("SELECT count(*) FROM pending_s2_core_missing").fetchone()[0]
    print(f"[Recover] 本轮补全完毕：已扫描 {requested} 篇，成功彻底补齐 {recovered} 篇，core_missing 剩余待办: {remaining} 篇", flush=True)

    if publish is not None:
        pending_publish = db.execute("SELECT count(*) FROM pending_publish").fetchone()[0]
        if pending_publish:
            print(f"[Publish] 正在发布补全的 {pending_publish} 篇记录到 Elasticsearch 与 Qdrant...", flush=True)
            await publish()

    return {"total": total_missing, "recovered": recovered, "remaining": remaining}

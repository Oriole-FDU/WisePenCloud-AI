"""协调 OAI 基础事实、S2 待补充记录、缓存失效及整轮发布游标。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from email.utils import parsedate_to_datetime
from enum import StrEnum
from xml.etree import ElementTree as ET

import duckdb
import orjson
from offline.arxiv_oai import ArxivOaiHarvest, ArxivOaiRecord, INITIAL_FROM
from offline.embeddings import INPUT_HASH_SQL, reconcile_embeddings
from offline.s2_api import S2SyncClient, S2SyncPaper

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
    reason: _DiagnosticReason,
    detail: dict,
) -> None:
    db.execute(
        """INSERT OR REPLACE INTO arxiv_sync_diagnostics
        VALUES (?, ?, ?, ?, ?::JSON)""",
        [
            response_date,
            oai_identifier,
            arxiv_id,
            reason.value,
            orjson.dumps(detail).decode(),
        ],
    )


def _apply_deletion(
    db: duckdb.DuckDBPyConnection,
    record: ArxivOaiRecord,
    arxiv_id: str,
) -> list[PaperIdentifier]:
    row = db.execute(
        "SELECT doi, document FROM arxiv WHERE arxiv_id=?", [arxiv_id]
    ).fetchone()
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
        other_doi_ids = [
            item[0]
            for item in db.execute(
                "SELECT arxiv_id FROM arxiv WHERE doi=? AND arxiv_id<>? ORDER BY arxiv_id",
                [projection.doi, projection.arxiv_id],
            ).fetchall()
        ]
    if other_doi_ids:
        _write_diagnostic(
            db,
            response_date,
            record.identifier,
            projection.arxiv_id,
            _DiagnosticReason.DOI_CONFLICT,
            {"doi": projection.doi, "other_arxiv_ids": other_doi_ids},
        )

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

    requested = 0
    enriched = 0
    invalidations = []
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset: offset + batch_size]
        identifiers = [PaperIdentifier(IdentifierKind.ARXIV, item[0]) for item in batch]
        synced = await client.lookup(identifiers)
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

        for (arxiv_id, doi, old_document), sync_result in zip(
            batch, synced, strict=True
        ):
            if sync_result is None:
                continue
            conflicts = _s2_lookup_conflicts(
                db, arxiv_id, doi, sync_result, duplicate_corpus_ids
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
                if _core_s2_missing(document) or needs_vector:
                    db.execute(
                        """INSERT OR REPLACE INTO pending_s2(arxiv_id, reason)
                        VALUES (?, ?)""",
                        [arxiv_id, _PendingS2Reason.CORE_MISSING.value],
                    )
                else:
                    db.execute("DELETE FROM pending_s2 WHERE arxiv_id=?", [arxiv_id])
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

    return requested, enriched, list(dict.fromkeys(invalidations))


async def sync_arxiv(
    db: duckdb.DuckDBPyConnection,
    harvester,
    client: S2SyncClient,
    *,
    from_date: str | None = None,
    batch_size: int = 100,
    publish: Callable[[], Awaitable[object]],
    cache: PaperLookupCache,
    arxiv_cache: PaperLookupCache,
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
    harvest: ArxivOaiHarvest = await harvester.list_records(from_date=effective_from)

    invalidations = []
    updated = 0
    deleted = 0
    for record in harvest.records:
        db.begin()
        try:
            try:
                arxiv_id = _normalize_oai_identifier(record.identifier)
            except ValueError as error:
                _write_diagnostic(
                    db,
                    harvest.response_date,
                    record.identifier,
                    None,
                    _DiagnosticReason.INVALID_ARXIV_ID,
                    {"message": str(error), "datestamp": record.datestamp},
                )
                db.commit()
                continue

            if record.deleted:
                aliases = _apply_deletion(db, record, arxiv_id)
                _record_arxiv_cache_invalidation(db, arxiv_id, aliases)
                invalidations.extend(aliases)
                deleted += 1
            else:
                projection = _project_record(record, arxiv_id)
                changed, aliases = _apply_projection(
                    db, projection, record, harvest.response_date
                )
                has_identity_conflict = any(
                    reason == _DiagnosticReason.ARXIV_ID_CONFLICT
                    for reason, _ in projection.diagnostics
                )
                if not has_identity_conflict:
                    updated += int(changed)
                    _record_arxiv_cache_invalidation(db, arxiv_id, aliases)
                    invalidations.extend(aliases)
            db.commit()
        except BaseException:
            db.rollback()
            raise

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
        await publish()
        if (
            db.execute("SELECT count(*) FROM pending_publish").fetchone()[0]
            or db.execute("SELECT count(*) FROM pending_delete").fetchone()[0]
        ):
            raise RuntimeError("arXiv publication returned with pending records")

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
    except BaseException:
        db.rollback()
        raise

    diagnostics = db.execute(
        "SELECT count(*) FROM arxiv_sync_diagnostics WHERE response_date=?",
        [harvest.response_date],
    ).fetchone()[0]
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

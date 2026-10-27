"""按 S2 release 差分替换本地关联事实，并在完整发布后推进游标。"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit

import duckdb
import httpx
import orjson

from scholar.domain.models.identifiers import IdentifierKind, PaperIdentifier
from scholar.etl.s2 import _create_schema as _create_s2_schema
from scholar.etl.s2 import _paper_values, apply_s2_diff_projection

PublishPending = Callable[[], Awaitable[dict]]
InvalidateAliases = Callable[[list[PaperIdentifier]], Awaitable[None]]


def _create_schema(db: duckdb.DuckDBPyConnection) -> None:
    _create_s2_schema(db)
    db.execute("""
        CREATE TABLE IF NOT EXISTS s2_diff_sync_state (
            singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),
            completed_release VARCHAR NOT NULL,
            target_release VARCHAR,
            tldr_model VARCHAR NOT NULL
        );
        CREATE TABLE IF NOT EXISTS s2_diff_edges (
            from_release VARCHAR NOT NULL,
            to_release VARCHAR NOT NULL,
            papers_inventory JSON NOT NULL,
            tldrs_inventory JSON NOT NULL,
            complete BOOLEAN NOT NULL DEFAULT false,
            PRIMARY KEY (from_release, to_release)
        );
        CREATE TABLE IF NOT EXISTS s2_diff_files (
            from_release VARCHAR NOT NULL,
            to_release VARCHAR NOT NULL,
            dataset VARCHAR NOT NULL,
            action VARCHAR NOT NULL,
            ordinal BIGINT NOT NULL,
            object_path VARCHAR NOT NULL,
            complete BOOLEAN NOT NULL DEFAULT false,
            scanned BIGINT NOT NULL DEFAULT 0,
            retained BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (from_release, to_release, dataset, action, ordinal)
        );
        CREATE TABLE IF NOT EXISTS s2_diff_current_papers (
            corpus_id VARCHAR PRIMARY KEY,
            arxiv_id VARCHAR NOT NULL,
            doi VARCHAR,
            citation_count BIGINT,
            influential_citation_count BIGINT,
            is_open_access BOOLEAN,
            source_release VARCHAR NOT NULL,
            source_path VARCHAR NOT NULL,
            source_line BIGINT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS s2_diff_current_tldrs (
            corpus_id VARCHAR PRIMARY KEY,
            model VARCHAR NOT NULL,
            text VARCHAR,
            source_release VARCHAR NOT NULL,
            source_path VARCHAR NOT NULL,
            source_line BIGINT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS s2_diff_affected (
            target_release VARCHAR NOT NULL,
            arxiv_id VARCHAR NOT NULL,
            corpus_id VARCHAR NOT NULL,
            doi VARCHAR,
            PRIMARY KEY (target_release, arxiv_id, corpus_id)
        );
        CREATE TABLE IF NOT EXISTS s2_diff_diagnostics (
            from_release VARCHAR NOT NULL,
            to_release VARCHAR NOT NULL,
            dataset VARCHAR NOT NULL,
            source_path VARCHAR NOT NULL,
            source_line BIGINT NOT NULL,
            reason VARCHAR NOT NULL,
            subject VARCHAR NOT NULL,
            detail JSON NOT NULL,
            PRIMARY KEY (
                from_release, to_release, dataset, source_path,
                source_line, reason, subject
            )
        );
        CREATE TEMP TABLE IF NOT EXISTS _s2_diff_paper_input (
            source_line BIGINT,
            corpus_id VARCHAR,
            arxiv_id VARCHAR,
            doi VARCHAR,
            citation_count BIGINT,
            influential_citation_count BIGINT,
            is_open_access BOOLEAN
        );
        CREATE TEMP TABLE IF NOT EXISTS _s2_diff_tldr_input (
            source_line BIGINT,
            corpus_id VARCHAR,
            text VARCHAR
        );
        CREATE TEMP TABLE IF NOT EXISTS _s2_diff_delete_input (
            source_line BIGINT,
            corpus_id VARCHAR
        );
    """)


def _baseline_model(db: duckdb.DuckDBPyConnection, release_id: str) -> str:
    release = db.execute(
        "SELECT tldr_model FROM s2_releases WHERE release_id=?", [release_id]
    ).fetchone()
    if release is None:
        raise ValueError("the baseline S2 release has not been imported")

    shards = db.execute(
        """SELECT count(*),
            count(*) FILTER (WHERE NOT complete),
            count(*) FILTER (WHERE dataset='papers')
        FROM s2_shards WHERE release_id=?""",
        [release_id],
    ).fetchone()
    arxiv_state = db.execute(
        "SELECT complete,published FROM import_state WHERE singleton=true"
    ).fetchone()
    if (
        shards[0] == 0
        or shards[1] != 0
        or shards[2] == 0
        or arxiv_state is None
        or not arxiv_state[0]
    ):
        raise ValueError("the baseline arXiv and S2 imports must reach EOF first")
    if (
        not arxiv_state[1]
        or db.execute("SELECT count(*) FROM pending_publish").fetchone()[0]
        or db.execute("SELECT count(*) FROM pending_delete").fetchone()[0]
    ):
        raise ValueError("the baseline arXiv and S2 projection must be fully published")

    mismatched = db.execute(
        """SELECT count(*) FROM arxiv
        WHERE json_extract(document,'$.metadata.s2') IS NOT NULL
            AND json_extract_string(document,'$.metadata.s2.release') IS DISTINCT FROM ?""",
        [release_id],
    ).fetchone()[0]
    invalid = db.execute(
        """SELECT count(*) FROM arxiv
        WHERE json_extract_string(document,'$.metadata.s2.release')=?
            AND (json_extract_string(document,'$.metadata.s2.corpus_id') IS NULL
                OR json_extract_string(document,'$.metadata.s2.paper_source.path') IS NULL
                OR try_cast(json_extract_string(
                    document,'$.metadata.s2.paper_source.line') AS BIGINT) IS NULL)""",
        [release_id],
    ).fetchone()[0]
    if mismatched or invalid:
        raise ValueError("the baseline S2 projection is inconsistent")
    return release[0]


def _seed_baseline(
    db: duckdb.DuckDBPyConnection, release_id: str, requested_model: str | None
) -> str:
    model = _baseline_model(db, release_id)
    if requested_model is not None and requested_model != model:
        raise ValueError("requested TLDR model differs from the baseline release")

    db.begin()
    try:
        db.execute(
            """INSERT INTO s2_diff_sync_state(
                singleton,completed_release,target_release,tldr_model
            ) VALUES (true,?,NULL,?)""",
            [release_id, model],
        )
        db.execute(
            """INSERT INTO s2_diff_current_papers
            SELECT
                json_extract_string(a.document,'$.metadata.s2.corpus_id'),
                a.arxiv_id, a.doi,
                try_cast(json_extract_string(
                    a.document,'$.metadata.s2.citation_count') AS BIGINT),
                try_cast(json_extract_string(
                    a.document,'$.metadata.s2.influential_citation_count') AS BIGINT),
                try_cast(json_extract_string(
                    a.document,'$.metadata.s2.is_open_access') AS BOOLEAN),
                coalesce(json_extract_string(
                    a.document,'$.metadata.s2.paper_source.release'), ?),
                json_extract_string(a.document,'$.metadata.s2.paper_source.path'),
                try_cast(json_extract_string(
                    a.document,'$.metadata.s2.paper_source.line') AS BIGINT)
            FROM arxiv a
            WHERE json_extract_string(a.document,'$.metadata.s2.release')=?""",
            [release_id, release_id],
        )
        db.execute(
            """INSERT INTO s2_diff_current_tldrs
            SELECT
                json_extract_string(a.document,'$.metadata.s2.corpus_id'),
                json_extract_string(a.document,'$.metadata.s2.tldr.model'),
                json_extract_string(a.document,'$.metadata.s2.tldr.text'),
                coalesce(json_extract_string(
                    a.document,'$.metadata.s2.tldr_source.release'), ?),
                json_extract_string(a.document,'$.metadata.s2.tldr_source.path'),
                try_cast(json_extract_string(
                    a.document,'$.metadata.s2.tldr_source.line') AS BIGINT)
            FROM arxiv a
            WHERE json_extract_string(a.document,'$.metadata.s2.release')=?
                AND json_type(json_extract(a.document,'$.metadata.s2.tldr'))='OBJECT'
                AND json_extract_string(a.document,'$.metadata.s2.tldr.model')=?""",
            [release_id, release_id, model],
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return model


def _stable_object_path(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("S2 diff files must use HTTPS signed URLs")
    if not parsed.path:
        raise ValueError("S2 diff file URL has no object path")
    # 签名 URL 查询串包含临时凭证；只保留稳定对象路径，不持久化查询参数。
    return f"{parsed.netloc}{parsed.path}"


def _edge_files(edge: dict) -> dict[str, list[tuple[str, str]]]:
    files = {}
    for action in ("delete", "update"):
        values = edge.get(f"{action}_files", [])
        if not isinstance(values, list) or any(
            not isinstance(url, str) for url in values
        ):
            raise ValueError("S2 diff file lists must contain signed URL strings")
        files[action] = [(url, _stable_object_path(url)) for url in values]
    return files


def _manifest_edges(response: dict, dataset: str, start: str, end: str) -> list[dict]:
    if (
        response.get("dataset") != dataset
        or response.get("start_release") != start
        or response.get("end_release") != end
        or not isinstance(response.get("diffs"), list)
    ):
        raise ValueError("S2 diff manifest does not match the requested release range")

    edges = response["diffs"]
    expected = start
    for edge in edges:
        if edge.get("from_release") != expected or not edge.get("to_release"):
            raise ValueError("S2 diff releases are not a continuous chain")
        _edge_files(edge)
        expected = edge["to_release"]
    if expected != end:
        raise ValueError("S2 diff manifest does not reach the requested release")
    return edges


def _inventory(edge: dict) -> dict[str, list[str]]:
    files = _edge_files(edge)
    return {
        action: [path for _, path in files[action]] for action in ("delete", "update")
    }


def _register_edge(
    db: duckdb.DuckDBPyConnection, paper_edge: dict, tldr_edge: dict
) -> None:
    from_release = paper_edge["from_release"]
    to_release = paper_edge["to_release"]
    paper_inventory = orjson.dumps(_inventory(paper_edge)).decode()
    tldr_inventory = orjson.dumps(_inventory(tldr_edge)).decode()
    row = db.execute(
        """SELECT papers_inventory,tldrs_inventory
        FROM s2_diff_edges WHERE from_release=? AND to_release=?""",
        [from_release, to_release],
    ).fetchone()
    if row is not None:
        saved = tuple(orjson.dumps(orjson.loads(value)).decode() for value in row)
        if saved != (paper_inventory, tldr_inventory):
            raise ValueError("S2 diff file inventory changed during resume")
        return

    db.execute(
        """INSERT INTO s2_diff_edges(
            from_release,to_release,papers_inventory,tldrs_inventory
        ) VALUES (?,?,?::JSON,?::JSON)""",
        [from_release, to_release, paper_inventory, tldr_inventory],
    )


def _register_file(
    db: duckdb.DuckDBPyConnection,
    from_release: str,
    to_release: str,
    dataset: str,
    action: str,
    ordinal: int,
    object_path: str,
) -> bool:
    row = db.execute(
        """SELECT object_path,complete FROM s2_diff_files
        WHERE from_release=? AND to_release=? AND dataset=? AND action=? AND ordinal=?""",
        [from_release, to_release, dataset, action, ordinal],
    ).fetchone()
    if row is None:
        db.execute(
            """INSERT INTO s2_diff_files(
                from_release,to_release,dataset,action,ordinal,object_path
            ) VALUES (?,?,?,?,?,?)""",
            [from_release, to_release, dataset, action, ordinal, object_path],
        )
        return False
    if row[0] != object_path:
        raise ValueError("S2 diff object path changed during resume")
    return row[1]


class _SignedUrlExpired(Exception):
    pass


async def _download(http: httpx.AsyncClient, url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    request = http.build_request("GET", url)
    request.headers.pop("x-api-key", None)
    try:
        response = await http.send(request, stream=True)
        try:
            if response.status_code == 403:
                raise _SignedUrlExpired
            if not response.is_success:
                raise RuntimeError(
                    f"S2 signed file download failed with HTTP {response.status_code}"
                )
            with partial.open("wb") as output:
                async for chunk in response.aiter_bytes():
                    output.write(chunk)
        finally:
            await response.aclose()
        partial.replace(destination)
    except httpx.RequestError as exc:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"S2 signed file download failed with {type(exc).__name__}"
        ) from None
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


async def _fresh_signed_url(
    client,
    from_release: str,
    to_release: str,
    dataset: str,
    action: str,
    object_path: str,
) -> str:
    manifest = await client.diffs(from_release, to_release, dataset)
    edges = _manifest_edges(manifest, dataset, from_release, to_release)
    edge = next(
        (
            item
            for item in edges
            if item["from_release"] == from_release and item["to_release"] == to_release
        ),
        None,
    )
    if edge is None:
        raise RuntimeError("refreshed S2 diff manifest no longer contains the edge")
    matches = [url for url, path in _edge_files(edge)[action] if path == object_path]
    if len(matches) != 1:
        raise RuntimeError("refreshed S2 manifest has no unique matching object path")
    return matches[0]


async def _download_with_refresh(
    http: httpx.AsyncClient,
    client,
    url: str,
    destination: Path,
    from_release: str,
    to_release: str,
    dataset: str,
    action: str,
    object_path: str,
) -> None:
    try:
        await _download(http, url, destination)
    except _SignedUrlExpired:
        refreshed_url = await _fresh_signed_url(
            client,
            from_release,
            to_release,
            dataset,
            action,
            object_path,
        )
        try:
            await _download(http, refreshed_url, destination)
        except _SignedUrlExpired:
            raise RuntimeError("refreshed S2 signed file URL also expired") from None


def _ensure_local_doi_map(db: duckdb.DuckDBPyConnection) -> None:
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _s2_diff_local_doi AS
        SELECT doi,count(*)::BIGINT AS candidate_count,min(arxiv_id) AS candidate_arxiv_id
        FROM arxiv WHERE doi IS NOT NULL GROUP BY doi
    """)


def _stage_papers(db: duckdb.DuckDBPyConnection, batch: list[dict]) -> None:
    db.execute(
        """INSERT INTO _s2_diff_paper_input
        SELECT CAST(value->>'source_line' AS BIGINT), value->>'corpus_id',
            value->>'arxiv_id', value->>'doi',
            CAST(value->>'citation_count' AS BIGINT),
            CAST(value->>'influential_citation_count' AS BIGINT),
            CAST(value->>'is_open_access' AS BOOLEAN)
        FROM json_each(?)""",
        [orjson.dumps(batch).decode()],
    )


def _stage_tldrs(db: duckdb.DuckDBPyConnection, batch: list[dict]) -> None:
    db.execute(
        """INSERT INTO _s2_diff_tldr_input
        SELECT CAST(value->>'source_line' AS BIGINT), value->>'corpus_id',
            value->>'text'
        FROM json_each(?)""",
        [orjson.dumps(batch).decode()],
    )


def _stage_deletes(db: duckdb.DuckDBPyConnection, batch: list[dict]) -> None:
    db.execute(
        """INSERT INTO _s2_diff_delete_input
        SELECT CAST(value->>'source_line' AS BIGINT), value->>'corpus_id'
        FROM json_each(?)""",
        [orjson.dumps(batch).decode()],
    )


def _apply_paper_input(
    db: duckdb.DuckDBPyConnection,
    target_release: str,
    from_release: str,
    to_release: str,
    source_path: str,
) -> int:
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _s2_diff_paper_resolution AS
        SELECT s.*,
            direct.arxiv_id AS direct_id,
            doi.candidate_count AS doi_candidate_count,
            doi.candidate_arxiv_id AS doi_candidate_id,
            CASE
                WHEN direct.arxiv_id IS NOT NULL THEN direct.arxiv_id
                WHEN s.arxiv_id IS NULL AND doi.candidate_count=1
                    THEN doi.candidate_arxiv_id
            END AS resolved_id
        FROM _s2_diff_paper_input s
        LEFT JOIN arxiv direct ON direct.arxiv_id=s.arxiv_id
        LEFT JOIN _s2_diff_local_doi doi ON doi.doi=s.doi
    """)
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _s2_diff_paper_conflicts AS
        SELECT corpus_id
        FROM _s2_diff_paper_input
        GROUP BY corpus_id
        HAVING count(DISTINCT to_json(struct_pack(
            arxiv_id := arxiv_id, doi := doi,
            citation_count := citation_count,
            influential_citation_count := influential_citation_count,
            is_open_access := is_open_access
        ))) > 1
    """)
    db.execute(
        """INSERT OR IGNORE INTO s2_diff_diagnostics
        SELECT ?,?,'papers',?,r.source_line,'corpus_id_conflict',
            r.corpus_id,to_json(struct_pack(
                corpus_id := r.corpus_id, arxiv_id := r.arxiv_id,
                doi := r.doi, citation_count := r.citation_count,
                influential_citation_count := r.influential_citation_count,
                is_open_access := r.is_open_access))
        FROM _s2_diff_paper_input r
        JOIN _s2_diff_paper_conflicts c USING(corpus_id)""",
        [from_release, to_release, source_path],
    )
    db.execute(
        """INSERT OR IGNORE INTO s2_diff_diagnostics
        SELECT ?,?,'papers',?,r.source_line,'doi_ambiguous',
            coalesce(r.corpus_id,''),to_json(struct_pack(
                corpus_id := r.corpus_id, doi := r.doi,
                candidate_count := r.doi_candidate_count))
        FROM _s2_diff_paper_resolution r
        WHERE r.arxiv_id IS NULL AND r.doi_candidate_count>1""",
        [from_release, to_release, source_path],
    )
    db.execute(
        """INSERT OR IGNORE INTO s2_diff_diagnostics
        SELECT ?,?,'papers',?,r.source_line,'arxiv_doi_conflict',
            coalesce(r.corpus_id,''),to_json(struct_pack(
                corpus_id := r.corpus_id, source_arxiv := r.arxiv_id,
                source_doi := r.doi, resolved_arxiv := r.resolved_id))
        FROM _s2_diff_paper_resolution r
        WHERE r.direct_id IS NOT NULL AND r.doi IS NOT NULL
            AND EXISTS (
                SELECT 1 FROM arxiv candidate
                WHERE candidate.doi=r.doi AND candidate.arxiv_id<>r.direct_id
            )""",
        [from_release, to_release, source_path],
    )
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _s2_diff_paper_canonical AS
        SELECT r.*
        FROM _s2_diff_paper_resolution r
        WHERE NOT EXISTS (
            SELECT 1 FROM _s2_diff_paper_conflicts c
            WHERE c.corpus_id=r.corpus_id
        )
        QUALIFY row_number() OVER (
            PARTITION BY r.corpus_id ORDER BY r.source_line
        )=1
    """)
    db.execute(
        """INSERT OR IGNORE INTO s2_diff_affected
        SELECT ?,p.arxiv_id,p.corpus_id,p.doi
        FROM s2_diff_current_papers p
        WHERE EXISTS (
            SELECT 1 FROM _s2_diff_paper_resolution r
            WHERE r.corpus_id=p.corpus_id OR r.resolved_id=p.arxiv_id
        )""",
        [target_release],
    )
    db.execute(
        """INSERT OR IGNORE INTO s2_diff_affected
        SELECT ?,resolved_id,corpus_id,doi
        FROM _s2_diff_paper_canonical WHERE resolved_id IS NOT NULL""",
        [target_release],
    )
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _s2_diff_replaced_papers AS
        SELECT p.*
        FROM s2_diff_current_papers p
        WHERE EXISTS (
            SELECT 1 FROM _s2_diff_paper_resolution r
            WHERE r.corpus_id=p.corpus_id OR r.resolved_id=p.arxiv_id
        )
    """)
    db.execute("""
        DELETE FROM s2_diff_current_tldrs
        WHERE corpus_id IN (
            SELECT old.corpus_id
            FROM _s2_diff_replaced_papers old
            WHERE NOT EXISTS (
                SELECT 1 FROM _s2_diff_paper_canonical incoming
                WHERE incoming.corpus_id=old.corpus_id
                    AND incoming.resolved_id=old.arxiv_id
            )
        )
    """)
    db.execute("""
        DELETE FROM s2_diff_current_papers
        WHERE corpus_id IN (SELECT corpus_id FROM _s2_diff_replaced_papers)
    """)
    db.execute(
        """INSERT INTO s2_diff_current_papers
        SELECT corpus_id,resolved_id,doi,citation_count,
            influential_citation_count,is_open_access,?, ?,source_line
        FROM _s2_diff_paper_canonical
        WHERE resolved_id IS NOT NULL""",
        [to_release, source_path],
    )
    retained = int(
        db.execute(
            "SELECT count(*) FROM _s2_diff_paper_canonical WHERE resolved_id IS NOT NULL"
        ).fetchone()[0]
    )
    return retained


def _apply_tldr_input(
    db: duckdb.DuckDBPyConnection,
    target_release: str,
    from_release: str,
    to_release: str,
    source_path: str,
    model: str,
) -> int:
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _s2_diff_tldr_conflicts AS
        SELECT corpus_id FROM _s2_diff_tldr_input
        GROUP BY corpus_id
        HAVING count(DISTINCT to_json(struct_pack(text := text)))>1
    """)
    db.execute(
        """INSERT OR IGNORE INTO s2_diff_diagnostics
        SELECT ?,?,'tldrs',?,r.source_line,'tldr_conflict',
            r.corpus_id,to_json(struct_pack(corpus_id := r.corpus_id, text := r.text))
        FROM _s2_diff_tldr_input r
        JOIN _s2_diff_tldr_conflicts c USING(corpus_id)""",
        [from_release, to_release, source_path],
    )
    db.execute(
        """INSERT OR IGNORE INTO s2_diff_affected
        SELECT ?,p.arxiv_id,p.corpus_id,p.doi
        FROM s2_diff_current_papers p
        JOIN _s2_diff_tldr_input incoming USING(corpus_id)""",
        [target_release],
    )
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _s2_diff_tldr_canonical AS
        SELECT * FROM _s2_diff_tldr_input
        WHERE NOT EXISTS (
            SELECT 1 FROM _s2_diff_tldr_conflicts c
            WHERE c.corpus_id=_s2_diff_tldr_input.corpus_id
        )
        QUALIFY row_number() OVER (
            PARTITION BY corpus_id ORDER BY source_line
        )=1
    """)
    db.execute(
        """DELETE FROM s2_diff_current_tldrs
        WHERE corpus_id IN (SELECT corpus_id FROM _s2_diff_tldr_input)"""
    )
    db.execute(
        """INSERT INTO s2_diff_current_tldrs
        SELECT incoming.corpus_id,?,incoming.text,?,?,incoming.source_line
        FROM _s2_diff_tldr_canonical incoming
        WHERE EXISTS (
            SELECT 1 FROM s2_diff_current_papers p
            WHERE p.corpus_id=incoming.corpus_id
        )""",
        [model, to_release, source_path],
    )
    return int(
        db.execute(
            """SELECT count(*) FROM _s2_diff_tldr_canonical incoming
            WHERE EXISTS (
                SELECT 1 FROM s2_diff_current_papers p
                WHERE p.corpus_id=incoming.corpus_id
            )"""
        ).fetchone()[0]
    )


def _apply_deletes(
    db: duckdb.DuckDBPyConnection,
    dataset: str,
    target_release: str,
) -> int:
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _s2_diff_delete_ids AS
        SELECT DISTINCT corpus_id FROM _s2_diff_delete_input
    """)
    if dataset == "papers":
        db.execute(
            """INSERT OR IGNORE INTO s2_diff_affected
            SELECT ?,p.arxiv_id,p.corpus_id,p.doi
            FROM s2_diff_current_papers p
            JOIN _s2_diff_delete_ids d USING(corpus_id)""",
            [target_release],
        )
        deleted = db.execute(
            """SELECT count(*) FROM s2_diff_current_papers p
            JOIN _s2_diff_delete_ids d USING(corpus_id)"""
        ).fetchone()[0]
        db.execute(
            """DELETE FROM s2_diff_current_tldrs
            WHERE corpus_id IN (SELECT corpus_id FROM _s2_diff_delete_ids)"""
        )
        db.execute(
            """DELETE FROM s2_diff_current_papers
            WHERE corpus_id IN (SELECT corpus_id FROM _s2_diff_delete_ids)"""
        )
    else:
        db.execute(
            """INSERT OR IGNORE INTO s2_diff_affected
            SELECT ?,p.arxiv_id,p.corpus_id,p.doi
            FROM s2_diff_current_papers p
            JOIN _s2_diff_delete_ids d USING(corpus_id)""",
            [target_release],
        )
        deleted = db.execute(
            """SELECT count(*) FROM s2_diff_current_tldrs t
            JOIN _s2_diff_delete_ids d USING(corpus_id)"""
        ).fetchone()[0]
        db.execute(
            """DELETE FROM s2_diff_current_tldrs
            WHERE corpus_id IN (SELECT corpus_id FROM _s2_diff_delete_ids)"""
        )
    return int(deleted)


def _record_progress(
    release: str,
    dataset: str,
    object_path: str,
    scanned: int,
    retained: int,
) -> None:
    print(
        orjson.dumps(
            {
                "stage": "s2_diff",
                "release": release,
                "dataset": dataset,
                "source_path": object_path,
                "scanned": scanned,
                "retained": retained,
                "complete": True,
            }
        ).decode(),
        flush=True,
    )


def _parse_delete_record(record: dict, source_line: int) -> dict:
    corpus_id = record.get("corpusid")
    if corpus_id is None:
        raise ValueError("S2 delete record must include corpusid")
    return {"source_line": source_line, "corpus_id": str(corpus_id)}


def _parse_tldr_record(record: dict, source_line: int, model: str) -> dict | None:
    if record.get("model") != model:
        return None
    corpus_id = record.get("corpusid")
    if corpus_id is None:
        raise ValueError("S2 TLDR record must include corpusid")
    if "text" not in record:
        raise ValueError("S2 TLDR record must include text, including explicit null")
    text = record.get("text")
    if text is not None and not isinstance(text, str):
        raise ValueError("S2 TLDR text must be a string or null")
    return {"source_line": source_line, "corpus_id": str(corpus_id), "text": text}


def _clear_staging(db: duckdb.DuckDBPyConnection) -> None:
    db.execute("DELETE FROM _s2_diff_paper_input")
    db.execute("DELETE FROM _s2_diff_tldr_input")
    db.execute("DELETE FROM _s2_diff_delete_input")


async def _process_file(
    db: duckdb.DuckDBPyConnection,
    client,
    http: httpx.AsyncClient,
    workdir: Path,
    target_release: str,
    from_release: str,
    to_release: str,
    dataset: str,
    action: str,
    ordinal: int,
    url: str,
    object_path: str,
    model: str,
    batch_size: int,
) -> bool:
    if _register_file(
        db,
        from_release,
        to_release,
        dataset,
        action,
        ordinal,
        object_path,
    ):
        return False

    cache_name = hashlib.sha256(object_path.encode()).hexdigest() + ".gz"
    destination = workdir / from_release / to_release / dataset / action / cache_name
    await _download_with_refresh(
        http,
        client,
        url,
        destination,
        from_release,
        to_release,
        dataset,
        action,
        object_path,
    )

    _clear_staging(db)
    scanned = 0
    retained = 0
    batch: list[dict] = []
    db.begin()
    try:
        with gzip.open(destination, "rb") as compressed:
            for source_line, line in enumerate(compressed, start=1):
                if not line.strip():
                    continue
                scanned += 1
                record = orjson.loads(line)
                if not isinstance(record, dict):
                    raise TypeError("S2 diff lines must contain JSON objects")

                if action == "delete":
                    batch.append(_parse_delete_record(record, source_line))
                    stage = _stage_deletes
                elif dataset == "papers":
                    values = _paper_values(record, source_line)
                    if values["corpus_id"] is None:
                        raise ValueError("S2 paper update must include corpusid")
                    batch.append(values)
                    stage = _stage_papers
                else:
                    values = _parse_tldr_record(record, source_line, model)
                    if values is None:
                        continue
                    batch.append(values)
                    stage = _stage_tldrs

                if len(batch) == batch_size:
                    stage(db, batch)
                    batch.clear()
        if batch:
            stage(db, batch)

        if action == "delete":
            retained = _apply_deletes(db, dataset, target_release)
        elif dataset == "papers":
            retained = _apply_paper_input(
                db, target_release, from_release, to_release, object_path
            )
        else:
            retained = _apply_tldr_input(
                db, target_release, from_release, to_release, object_path, model
            )

        _clear_staging(db)
        db.execute(
            """UPDATE s2_diff_files SET complete=true,scanned=?,retained=?
            WHERE from_release=? AND to_release=? AND dataset=?
                AND action=? AND ordinal=?""",
            [
                scanned,
                retained,
                from_release,
                to_release,
                dataset,
                action,
                ordinal,
            ],
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise

    _record_progress(target_release, dataset, object_path, scanned, retained)
    return True


async def _apply_edge_files(
    db: duckdb.DuckDBPyConnection,
    client,
    http: httpx.AsyncClient,
    workdir: Path,
    target_release: str,
    edge: dict,
    dataset: str,
    model: str,
    batch_size: int,
) -> int:
    completed_files = 0
    files = _edge_files(edge)
    # 同一 CorpusID 同时出现删除和更新时，更新是该 release 的最终事实。
    for action in ("delete", "update"):
        for ordinal, (url, object_path) in enumerate(files[action]):
            completed_files += await _process_file(
                db,
                client,
                http,
                workdir,
                target_release,
                edge["from_release"],
                edge["to_release"],
                dataset,
                action,
                ordinal,
                url,
                object_path,
                model,
                batch_size,
            )
    return completed_files


def _aliases_for_target(
    db: duckdb.DuckDBPyConnection, target_release: str
) -> list[PaperIdentifier]:
    rows = db.execute(
        """SELECT arxiv_id,corpus_id,doi FROM s2_diff_affected
        WHERE target_release=? ORDER BY arxiv_id,corpus_id,doi""",
        [target_release],
    ).fetchall()
    identifiers = []
    seen = set()
    for arxiv_id, corpus_id, doi in rows:
        candidates = [
            PaperIdentifier(IdentifierKind.ARXIV, arxiv_id),
            PaperIdentifier(IdentifierKind.CORPUS_ID, corpus_id),
        ]
        if doi:
            candidates.append(PaperIdentifier(IdentifierKind.DOI, doi))
        for identifier in candidates:
            if identifier not in seen:
                seen.add(identifier)
                identifiers.append(identifier)
    return identifiers


def _diagnose_ambiguous_arxiv_links(
    db: duckdb.DuckDBPyConnection, from_release: str, to_release: str
) -> None:
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _s2_diff_current_arxiv_conflicts AS
        SELECT arxiv_id,count(*)::BIGINT AS candidate_count
        FROM s2_diff_current_papers
        GROUP BY arxiv_id HAVING count(*)>1
    """)
    db.execute(
        """INSERT OR IGNORE INTO s2_diff_diagnostics
        SELECT ?,?,'papers',p.source_path,p.source_line,
            'arxiv_corpus_ambiguous',p.arxiv_id,
            to_json(struct_pack(
                arxiv_id := p.arxiv_id, corpus_id := p.corpus_id,
                candidate_count := c.candidate_count))
        FROM s2_diff_current_papers p
        JOIN _s2_diff_current_arxiv_conflicts c USING(arxiv_id)""",
        [from_release, to_release],
    )


def _publication_completed(status: dict) -> bool:
    return (
        status.get("pending") == 0
        and status.get("pending_delete", 0) == 0
        and isinstance(status.get("import"), dict)
        and status["import"].get("published") is True
    )


async def sync_s2_diffs(
    db: duckdb.DuckDBPyConnection,
    client,
    http: httpx.AsyncClient,
    workdir: str | Path,
    *,
    publish_pending: PublishPending,
    invalidate_aliases: InvalidateAliases,
    base_release: str | None = None,
    target_release: str | None = None,
    tldr_model: str | None = None,
    batch_size: int = 50_000,
) -> dict:
    """从完整 P2 快照起步，重放连续差分，所有发布副作用成功后推进游标。

    每个对象文件仅在 gzip EOF/CRC 校验通过时提交；失败时该文件从头重读。
    已完成 target 会冻结到状态表，重启不会因为 latest 前进而切换同步目标。
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    _create_schema(db)
    _ensure_local_doi_map(db)
    state = db.execute(
        """SELECT completed_release,target_release,tldr_model
        FROM s2_diff_sync_state WHERE singleton=true"""
    ).fetchone()
    if state is None:
        if base_release is None:
            raise ValueError("base_release is required to seed the first diff sync")
        model = _seed_baseline(db, base_release, tldr_model)
        completed_release, frozen_target = base_release, None
    else:
        completed_release, frozen_target, model = state
        if tldr_model is not None and tldr_model != model:
            raise ValueError("requested TLDR model differs from the frozen sync model")

    if frozen_target is not None:
        if target_release is not None and target_release != frozen_target:
            raise ValueError("an in-progress S2 target release is frozen")
        target = frozen_target
    elif target_release is None:
        latest = await client.release("latest")
        target = latest["release_id"]
    else:
        target = target_release
    if not isinstance(target, str) or not target:
        raise ValueError("S2 target release must be a non-empty release ID")
    if target == completed_release:
        publish_status = await publish_pending()
        return {
            "complete": isinstance(publish_status, dict)
            and _publication_completed(publish_status),
            "completed_release": completed_release,
            "target_release": None,
            "processed_files": 0,
            "pending": db.execute("SELECT count(*) FROM pending_publish").fetchone()[0],
            "publish": publish_status,
        }
    if frozen_target is None:
        db.execute(
            "UPDATE s2_diff_sync_state SET target_release=? WHERE singleton=true",
            [target],
        )

    paper_response, tldr_response = await asyncio.gather(
        client.diffs(completed_release, target, "papers"),
        client.diffs(completed_release, target, "tldrs"),
    )
    paper_edges = _manifest_edges(paper_response, "papers", completed_release, target)
    tldr_edges = _manifest_edges(tldr_response, "tldrs", completed_release, target)
    paper_chain = [(edge["from_release"], edge["to_release"]) for edge in paper_edges]
    tldr_chain = [(edge["from_release"], edge["to_release"]) for edge in tldr_edges]
    if paper_chain != tldr_chain:
        raise ValueError(
            "papers and tldrs diff manifests have different release chains"
        )

    for paper_edge, tldr_edge in zip(paper_edges, tldr_edges, strict=True):
        _register_edge(db, paper_edge, tldr_edge)

    processed_files = 0
    workdir = Path(workdir)
    for paper_edge, tldr_edge in zip(paper_edges, tldr_edges, strict=True):
        edge_row = db.execute(
            """SELECT complete FROM s2_diff_edges
            WHERE from_release=? AND to_release=?""",
            [paper_edge["from_release"], paper_edge["to_release"]],
        ).fetchone()
        if edge_row[0]:
            continue
        processed_files += await _apply_edge_files(
            db,
            client,
            http,
            workdir,
            target,
            paper_edge,
            "papers",
            model,
            batch_size,
        )
        processed_files += await _apply_edge_files(
            db,
            client,
            http,
            workdir,
            target,
            tldr_edge,
            "tldrs",
            model,
            batch_size,
        )
        db.execute(
            """UPDATE s2_diff_edges SET complete=true
            WHERE from_release=? AND to_release=?""",
            [paper_edge["from_release"], paper_edge["to_release"]],
        )

    for edge in paper_edges:
        complete = db.execute(
            """SELECT complete FROM s2_diff_edges
            WHERE from_release=? AND to_release=?""",
            [edge["from_release"], edge["to_release"]],
        ).fetchone()[0]
        if not complete:
            raise RuntimeError("S2 diff chain has incomplete release edges")

    _diagnose_ambiguous_arxiv_links(db, completed_release, target)
    projection = apply_s2_diff_projection(db, target)
    publish_status = await publish_pending()
    if not isinstance(publish_status, dict) or not _publication_completed(
        publish_status
    ):
        return {
            "complete": False,
            "completed_release": completed_release,
            "target_release": target,
            "processed_files": processed_files,
            "projection": projection,
            "publish": publish_status,
        }

    aliases = _aliases_for_target(db, target)
    await invalidate_aliases(aliases)

    db.begin()
    try:
        db.execute(
            """UPDATE s2_diff_sync_state
            SET completed_release=?,target_release=NULL
            WHERE singleton=true AND completed_release=? AND target_release=?""",
            [target, completed_release, target],
        )
        db.execute("DELETE FROM s2_diff_affected WHERE target_release=?", [target])
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return {
        "complete": True,
        "completed_release": target,
        "target_release": None,
        "processed_files": processed_files,
        "projection": projection,
        "invalidated_aliases": len(aliases),
    }

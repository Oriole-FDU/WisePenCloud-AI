"""从压缩 S2 数据集中选择性导入本地论文事实，并投影到 arXiv 文档。"""

from __future__ import annotations

import gzip
from pathlib import Path

import duckdb
import orjson
from scholar.domain.models.identifiers import normalize_arxiv, normalize_doi

_DEFAULT_TLDR_MODEL = "tldr@v2.0.0"
_DEFAULT_BATCH_SIZE = 50_000
_PROJECTION_TARGET_BATCH_SIZE = 5_000


def _create_schema(db: duckdb.DuckDBPyConnection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS s2_releases (
            release_id VARCHAR PRIMARY KEY,
            tldr_model VARCHAR NOT NULL
        );
        CREATE TABLE IF NOT EXISTS s2_shards (
            release_id VARCHAR NOT NULL,
            dataset VARCHAR NOT NULL,
            source_path VARCHAR NOT NULL,
            source_size BIGINT NOT NULL,
            source_mtime_ns BIGINT NOT NULL,
            complete BOOLEAN NOT NULL DEFAULT false,
            scanned BIGINT NOT NULL DEFAULT 0,
            retained BIGINT NOT NULL DEFAULT 0,
            matched BIGINT NOT NULL DEFAULT 0,
            ambiguous BIGINT NOT NULL DEFAULT 0,
            conflicts BIGINT NOT NULL DEFAULT 0,
            missing BIGINT NOT NULL DEFAULT 0,
            filtered_model BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (release_id, dataset, source_path)
        );
        CREATE TABLE IF NOT EXISTS s2_papers (
            release_id VARCHAR NOT NULL,
            arxiv_id VARCHAR NOT NULL,
            corpus_id VARCHAR,
            doi VARCHAR,
            citation_count BIGINT,
            influential_citation_count BIGINT,
            is_open_access BOOLEAN,
            source_path VARCHAR NOT NULL,
            source_line BIGINT NOT NULL,
            PRIMARY KEY (release_id, source_path, source_line)
        );
        CREATE TABLE IF NOT EXISTS s2_tldrs (
            release_id VARCHAR NOT NULL,
            corpus_id VARCHAR NOT NULL,
            model VARCHAR NOT NULL,
            text VARCHAR,
            source_path VARCHAR NOT NULL,
            source_line BIGINT NOT NULL,
            PRIMARY KEY (release_id, source_path, source_line)
        );
        CREATE TABLE IF NOT EXISTS s2_diagnostics (
            release_id VARCHAR NOT NULL,
            dataset VARCHAR NOT NULL,
            source_path VARCHAR NOT NULL,
            source_line BIGINT NOT NULL,
            reason VARCHAR NOT NULL,
            subject VARCHAR NOT NULL,
            detail JSON NOT NULL,
            PRIMARY KEY (release_id, dataset, source_path, source_line, reason, subject)
        );
    """)


def _inventory(directory: str | Path, dataset: str) -> list[tuple]:
    root = Path(directory)
    if not root.exists():
        raise FileNotFoundError(f"S2 {dataset} directory does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"S2 {dataset} path is not a directory: {root}")
    inventory = []
    for path in sorted(
        root.rglob("*.gz"), key=lambda item: item.relative_to(root).as_posix()
    ):
        stat = path.stat()
        inventory.append(
            (dataset, path.relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns)
        )
    return inventory


def _register_release(
    db: duckdb.DuckDBPyConnection,
    release_id: str,
    tldr_model: str,
    inventory: list[tuple],
) -> None:
    row = db.execute(
        "SELECT tldr_model FROM s2_releases WHERE release_id=?", [release_id]
    ).fetchone()
    if row is None:
        db.begin()
        try:
            db.execute(
                "INSERT INTO s2_releases(release_id,tldr_model) VALUES (?,?)",
                [release_id, tldr_model],
            )
            if inventory:
                db.executemany(
                    "INSERT INTO s2_shards(release_id,dataset,source_path,source_size,source_mtime_ns) VALUES (?,?,?,?,?)",
                    [(release_id, *item) for item in inventory],
                )
            db.commit()
        except BaseException:
            db.rollback()
            raise
        return

    if row[0] != tldr_model:
        raise ValueError("S2 release was registered with a different TLDR model")
    saved = db.execute(
        "SELECT dataset,source_path,source_size,source_mtime_ns FROM s2_shards WHERE release_id=? ORDER BY dataset,source_path",
        [release_id],
    ).fetchall()
    if saved != sorted(inventory):
        raise ValueError("S2 shard inventory differs from the registered release")


def _create_temp_tables(
    db: duckdb.DuckDBPyConnection, *, include_doi_map: bool
) -> None:
    if include_doi_map:
        # DOI 候选在一次导入中只扫描基础 arXiv 表一次；各批次关联仍由 DuckDB 完成。
        db.execute("""
            CREATE OR REPLACE TEMP TABLE _s2_local_doi AS
            SELECT doi, count(*)::BIGINT AS candidate_count, min(arxiv_id) AS candidate_arxiv_id
            FROM arxiv
            WHERE doi IS NOT NULL
            GROUP BY doi;
        """)
    db.execute("""
        CREATE TEMP TABLE IF NOT EXISTS _s2_paper_batch (
            source_line BIGINT,
            corpus_id VARCHAR,
            arxiv_id VARCHAR,
            doi VARCHAR,
            citation_count BIGINT,
            influential_citation_count BIGINT,
            is_open_access BOOLEAN
        );
        CREATE TEMP TABLE IF NOT EXISTS _s2_tldr_batch (
            source_line BIGINT,
            corpus_id VARCHAR,
            text VARCHAR
        );
    """)


def _normalized_or_none(value, normalizer) -> str | None:
    if not value:
        return None
    try:
        return normalizer(value)
    except ValueError:
        return None


def _optional_integer(record: dict, name: str) -> int | None:
    value = record.get(name)
    if value is not None and type(value) is not int:
        raise ValueError(f"S2 papers field {name} must be an integer or null")
    return value


def _optional_boolean(record: dict, name: str) -> bool | None:
    value = record.get(name)
    if value is not None and type(value) is not bool:
        raise ValueError(f"S2 papers field {name} must be a boolean or null")
    return value


def _paper_values(record: dict, source_line: int) -> dict:
    external_ids = record.get("externalids") or {}
    return {
        "source_line": source_line,
        "corpus_id": None
        if record.get("corpusid") is None
        else str(record["corpusid"]),
        "arxiv_id": _normalized_or_none(external_ids.get("ArXiv"), normalize_arxiv),
        "doi": _normalized_or_none(external_ids.get("DOI"), normalize_doi),
        "citation_count": _optional_integer(record, "citationcount"),
        "influential_citation_count": _optional_integer(
            record, "influentialcitationcount"
        ),
        "is_open_access": _optional_boolean(record, "isopenaccess"),
    }


def _stage_paper_batch(db: duckdb.DuckDBPyConnection, batch: list[dict]) -> None:
    # 一个有界 JSON 批次只绑定一次，避免 DuckDB Python 驱动逐行适配每个字段。
    db.execute(
        """INSERT INTO _s2_paper_batch
        SELECT CAST(value->>'source_line' AS BIGINT), value->>'corpus_id',
            value->>'arxiv_id', value->>'doi',
            CAST(value->>'citation_count' AS BIGINT),
            CAST(value->>'influential_citation_count' AS BIGINT),
            CAST(value->>'is_open_access' AS BOOLEAN)
        FROM json_each(?)""",
        [orjson.dumps(batch).decode()],
    )


def _stage_tldr_batch(db: duckdb.DuckDBPyConnection, batch: list[dict]) -> None:
    db.execute(
        """INSERT INTO _s2_tldr_batch
        SELECT CAST(value->>'source_line' AS BIGINT),
            value->>'corpus_id', value->>'text'
        FROM json_each(?)""",
        [orjson.dumps(batch).decode()],
    )


def _create_corpus_candidate_map(
    db: duckdb.DuckDBPyConnection, release_id: str
) -> None:
    # papers 已全量到 EOF；缓存本 release 的 CorpusId 候选，避免每批重聚合事实表。
    db.execute(
        """CREATE OR REPLACE TEMP TABLE _s2_corpus_candidates AS
        SELECT corpus_id, count(DISTINCT arxiv_id)::BIGINT AS candidate_count
        FROM s2_papers
        WHERE release_id=? AND corpus_id IS NOT NULL
        GROUP BY corpus_id""",
        [release_id],
    )


def _print_progress(
    release_id: str,
    dataset: str,
    source_path: str,
    scanned: int,
    retained: int,
    complete: bool,
) -> None:
    print(
        orjson.dumps(
            {
                "stage": "s2_import",
                "release": release_id,
                "dataset": dataset,
                "source_path": source_path,
                "scanned": scanned,
                "retained": retained,
                "complete": complete,
            }
        ).decode(),
        flush=True,
    )


def _process_paper_batch(
    db: duckdb.DuckDBPyConnection, release_id: str, source_path: str
) -> tuple[int, int, int, int]:
    # arXiv 是强匹配；仅缺少可用 arXiv ID 时才允许唯一 DOI 回退。
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _s2_paper_resolution AS
        SELECT s.*,
            direct.arxiv_id AS direct_id,
            doi.candidate_count AS doi_candidate_count,
            doi.candidate_arxiv_id AS doi_candidate_id,
            CASE
                WHEN direct.arxiv_id IS NOT NULL THEN direct.arxiv_id
                WHEN s.arxiv_id IS NULL AND doi.candidate_count=1 THEN doi.candidate_arxiv_id
            END AS resolved_id,
            CASE
                WHEN direct.arxiv_id IS NOT NULL THEN 'matched_arxiv'
                WHEN s.arxiv_id IS NULL AND doi.candidate_count=1 THEN 'matched_doi'
                WHEN s.arxiv_id IS NULL AND doi.candidate_count>1 THEN 'ambiguous'
                WHEN s.arxiv_id IS NOT NULL AND doi.candidate_count>0 THEN 'conflict'
                ELSE 'missing'
            END AS outcome
        FROM _s2_paper_batch s
        LEFT JOIN arxiv direct ON direct.arxiv_id=s.arxiv_id
        LEFT JOIN _s2_local_doi doi ON doi.doi=s.doi;
    """)
    db.execute(
        """INSERT INTO s2_papers
        SELECT ?, resolved_id, corpus_id, doi, citation_count,
            influential_citation_count, is_open_access, ?, source_line
        FROM _s2_paper_resolution
        WHERE resolved_id IS NOT NULL""",
        [release_id, source_path],
    )
    db.execute(
        """INSERT OR IGNORE INTO s2_diagnostics
        SELECT ?, 'papers', ?, source_line, 'doi_ambiguous', coalesce(corpus_id,''),
            to_json(struct_pack(source_arxiv := arxiv_id, source_doi := doi,
                doi_candidate_count := doi_candidate_count))
        FROM _s2_paper_resolution WHERE outcome='ambiguous'""",
        [release_id, source_path],
    )
    db.execute(
        """INSERT OR IGNORE INTO s2_diagnostics
        SELECT ?, 'papers', ?, source_line, 'arxiv_doi_conflict', coalesce(corpus_id,''),
            to_json(struct_pack(source_arxiv := arxiv_id, source_doi := doi,
                doi_candidate_count := doi_candidate_count, resolved_arxiv := resolved_id))
        FROM _s2_paper_resolution r
        WHERE r.outcome='conflict' OR (r.direct_id IS NOT NULL AND r.doi IS NOT NULL
            AND EXISTS (SELECT 1 FROM arxiv candidate
                WHERE candidate.doi=r.doi AND candidate.arxiv_id<>r.direct_id))""",
        [release_id, source_path],
    )
    metrics = db.execute("""
        SELECT
            sum(CASE WHEN resolved_id IS NOT NULL THEN 1 ELSE 0 END),
            sum(CASE WHEN outcome='ambiguous' THEN 1 ELSE 0 END),
            sum(CASE WHEN outcome='conflict' OR (direct_id IS NOT NULL AND doi IS NOT NULL
                AND EXISTS (SELECT 1 FROM arxiv candidate
                    WHERE candidate.doi=_s2_paper_resolution.doi
                        AND candidate.arxiv_id<>_s2_paper_resolution.direct_id)) THEN 1 ELSE 0 END),
            sum(CASE WHEN outcome='missing' THEN 1 ELSE 0 END)
        FROM _s2_paper_resolution
    """).fetchone()
    db.execute("DELETE FROM _s2_paper_batch")
    return tuple(int(value or 0) for value in metrics)


def _process_tldr_batch(
    db: duckdb.DuckDBPyConnection,
    release_id: str,
    model: str,
    source_path: str,
) -> tuple[int, int, int, int]:
    db.execute(
        """CREATE OR REPLACE TEMP TABLE _s2_tldr_resolution AS
        SELECT s.*, papers.candidate_count
        FROM _s2_tldr_batch s
        LEFT JOIN _s2_corpus_candidates papers USING(corpus_id)"""
    )
    db.execute(
        """INSERT INTO s2_tldrs
        SELECT ?, corpus_id, ?, text, ?, source_line
        FROM _s2_tldr_resolution WHERE candidate_count>0""",
        [release_id, model, source_path],
    )
    db.execute(
        """INSERT OR IGNORE INTO s2_diagnostics
        SELECT ?, 'tldrs', ?, source_line, 'corpus_id_ambiguous', corpus_id,
            to_json(struct_pack(corpus_id := corpus_id, paper_candidate_count := candidate_count))
        FROM _s2_tldr_resolution WHERE candidate_count>1""",
        [release_id, source_path],
    )
    metrics = db.execute("""
        SELECT
            sum(CASE WHEN candidate_count>0 THEN 1 ELSE 0 END),
            sum(CASE WHEN candidate_count=1 THEN 1 ELSE 0 END),
            sum(CASE WHEN candidate_count>1 THEN 1 ELSE 0 END),
            sum(CASE WHEN candidate_count IS NULL THEN 1 ELSE 0 END)
        FROM _s2_tldr_resolution
    """).fetchone()
    db.execute("DELETE FROM _s2_tldr_batch")
    return tuple(int(value or 0) for value in metrics)


def _import_paper_shard(
    db: duckdb.DuckDBPyConnection,
    path: Path,
    release_id: str,
    source_path: str,
    expected_size: int,
    expected_mtime_ns: int,
    batch_size: int,
) -> None:
    counters = {
        "scanned": 0,
        "retained": 0,
        "ambiguous": 0,
        "conflicts": 0,
        "missing": 0,
    }
    batch = []
    db.begin()
    try:
        with gzip.open(path, "rb") as stream:
            for source_line, line in enumerate(stream, start=1):
                counters["scanned"] += 1
                batch.append(_paper_values(orjson.loads(line), source_line))
                if len(batch) == batch_size:
                    _stage_paper_batch(db, batch)
                    values = _process_paper_batch(db, release_id, source_path)
                    for key, value in zip(
                        ("retained", "ambiguous", "conflicts", "missing"),
                        values,
                        strict=True,
                    ):
                        counters[key] += value
                    batch.clear()
                    _print_progress(
                        release_id,
                        "papers",
                        source_path,
                        counters["scanned"],
                        counters["retained"],
                        False,
                    )
        if batch:
            _stage_paper_batch(db, batch)
            values = _process_paper_batch(db, release_id, source_path)
            for key, value in zip(
                ("retained", "ambiguous", "conflicts", "missing"), values, strict=True
            ):
                counters[key] += value
            _print_progress(
                release_id,
                "papers",
                source_path,
                counters["scanned"],
                counters["retained"],
                False,
            )
        current = path.stat()
        if (current.st_size, current.st_mtime_ns) != (expected_size, expected_mtime_ns):
            raise ValueError("S2 shard changed while it was being imported")
        db.execute(
            """UPDATE s2_shards SET complete=true, scanned=?, retained=?, matched=?,
                ambiguous=?, conflicts=?, missing=?
            WHERE release_id=? AND dataset='papers' AND source_path=?""",
            [
                counters["scanned"],
                counters["retained"],
                counters["retained"],
                counters["ambiguous"],
                counters["conflicts"],
                counters["missing"],
                release_id,
                source_path,
            ],
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    _print_progress(
        release_id,
        "papers",
        source_path,
        counters["scanned"],
        counters["retained"],
        True,
    )


def _import_tldr_shard(
    db: duckdb.DuckDBPyConnection,
    path: Path,
    release_id: str,
    model: str,
    source_path: str,
    expected_size: int,
    expected_mtime_ns: int,
    batch_size: int,
) -> None:
    counters = {
        "scanned": 0,
        "retained": 0,
        "matched": 0,
        "ambiguous": 0,
        "missing": 0,
        "filtered_model": 0,
    }
    batch = []
    db.begin()
    try:
        with gzip.open(path, "rb") as stream:
            for source_line, line in enumerate(stream, start=1):
                counters["scanned"] += 1
                record = orjson.loads(line)
                if record.get("model") != model:
                    counters["filtered_model"] += 1
                    continue
                text = record.get("text")
                if text is not None and not isinstance(text, str):
                    raise ValueError("S2 TLDR text must be a string or null")
                corpus_id = record.get("corpusid")
                batch.append(
                    {
                        "source_line": source_line,
                        "corpus_id": None if corpus_id is None else str(corpus_id),
                        "text": text,
                    }
                )
                if len(batch) == batch_size:
                    _stage_tldr_batch(db, batch)
                    values = _process_tldr_batch(db, release_id, model, source_path)
                    for key, value in zip(
                        ("retained", "matched", "ambiguous", "missing"),
                        values,
                        strict=True,
                    ):
                        counters[key] += value
                    batch.clear()
                    _print_progress(
                        release_id,
                        "tldrs",
                        source_path,
                        counters["scanned"],
                        counters["retained"],
                        False,
                    )
        if batch:
            _stage_tldr_batch(db, batch)
            values = _process_tldr_batch(db, release_id, model, source_path)
            for key, value in zip(
                ("retained", "matched", "ambiguous", "missing"), values, strict=True
            ):
                counters[key] += value
            _print_progress(
                release_id,
                "tldrs",
                source_path,
                counters["scanned"],
                counters["retained"],
                False,
            )
        current = path.stat()
        if (current.st_size, current.st_mtime_ns) != (expected_size, expected_mtime_ns):
            raise ValueError("S2 shard changed while it was being imported")
        db.execute(
            """UPDATE s2_shards SET complete=true, scanned=?, retained=?, matched=?,
                ambiguous=?, missing=?, filtered_model=?
            WHERE release_id=? AND dataset='tldrs' AND source_path=?""",
            [
                counters["scanned"],
                counters["retained"],
                counters["matched"],
                counters["ambiguous"],
                counters["missing"],
                counters["filtered_model"],
                release_id,
                source_path,
            ],
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    _print_progress(
        release_id,
        "tldrs",
        source_path,
        counters["scanned"],
        counters["retained"],
        True,
    )


def _status(db: duckdb.DuckDBPyConnection, release_id: str) -> dict:
    model = db.execute(
        "SELECT tldr_model FROM s2_releases WHERE release_id=?", [release_id]
    ).fetchone()[0]
    totals = {
        dataset: {
            "scanned": 0,
            "retained": 0,
            "matched": 0,
            "ambiguous": 0,
            "conflicts": 0,
            "missing": 0,
            "filtered_model": 0,
            "shards": 0,
            "completed_shards": 0,
        }
        for dataset in ("papers", "tldrs")
    }
    rows = db.execute(
        """SELECT dataset, count(*), sum(CASE WHEN complete THEN 1 ELSE 0 END),
            sum(scanned), sum(retained), sum(matched), sum(ambiguous),
            sum(conflicts), sum(missing), sum(filtered_model)
        FROM s2_shards WHERE release_id=? GROUP BY dataset""",
        [release_id],
    ).fetchall()
    for row in rows:
        (
            dataset,
            shards,
            completed,
            scanned,
            retained,
            matched,
            ambiguous,
            conflicts,
            missing,
            filtered,
        ) = row
        totals[dataset] = {
            "scanned": int(scanned or 0),
            "retained": int(retained or 0),
            "matched": int(matched or 0),
            "ambiguous": int(ambiguous or 0),
            "conflicts": int(conflicts or 0),
            "missing": int(missing or 0),
            "filtered_model": int(filtered or 0),
            "shards": shards,
            "completed_shards": int(completed or 0),
        }
    total_shards = sum(value["shards"] for value in totals.values())
    completed_shards = sum(value["completed_shards"] for value in totals.values())
    return {
        "release_id": release_id,
        "tldr_model": model,
        "complete": total_shards == completed_shards,
        "shards": total_shards,
        "completed_shards": completed_shards,
        "diagnostics": db.execute(
            "SELECT count(*) FROM s2_diagnostics WHERE release_id=?", [release_id]
        ).fetchone()[0],
        "papers": totals["papers"],
        "tldrs": totals["tldrs"],
    }


def import_s2(
    db: duckdb.DuckDBPyConnection,
    papers_dir: str | Path,
    tldrs_dir: str | Path,
    *,
    release_id: str,
    tldr_model: str = _DEFAULT_TLDR_MODEL,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    max_shards: int | None = None,
) -> dict:
    """流式导入一个固定目录清单；当前失败分片整片回滚并在重试时从头读取。

    每条输入只保留选择字段。论文关联优先使用精确 arXiv；无 arXiv 标识时才用唯一 DOI。
    即使该精确 arXiv 的 DOI 指向其他本地候选，信号仍归该 arXiv，另留身份诊断；不转派给 DOI 候选。
    papers 必须至少有一个 gzip 分片；tldrs 可为空以支持小样本导入，生产调用方仍需校验其完整清单。
    一次调用只有在所有 papers 与 tldrs 分片均达到 gzip EOF 后，才允许应用 ES 投影。
    """
    if not release_id or not tldr_model:
        raise ValueError("release_id and tldr_model are required")
    if batch_size < 1 or (max_shards is not None and max_shards < 1):
        raise ValueError("batch_size and max_shards must be positive")

    inventory = _inventory(papers_dir, "papers") + _inventory(tldrs_dir, "tldrs")
    inventory.sort()
    if not any(dataset == "papers" for dataset, *_ in inventory):
        raise ValueError("papers_dir must contain at least one gzip shard")
    _create_schema(db)
    _register_release(db, release_id, tldr_model, inventory)
    pending_papers = db.execute(
        "SELECT count(*) FROM s2_shards WHERE release_id=? AND dataset='papers' AND NOT complete",
        [release_id],
    ).fetchone()[0]
    pending_shards = db.execute(
        "SELECT count(*) FROM s2_shards WHERE release_id=? AND NOT complete",
        [release_id],
    ).fetchone()[0]
    if not pending_shards:
        return _status(db, release_id)
    _create_temp_tables(db, include_doi_map=bool(pending_papers))

    processed = 0
    for dataset, directory in (
        ("papers", Path(papers_dir)),
        ("tldrs", Path(tldrs_dir)),
    ):
        if dataset == "tldrs":
            pending_tldrs = db.execute(
                "SELECT count(*) FROM s2_shards WHERE release_id=? AND dataset='tldrs' AND NOT complete",
                [release_id],
            ).fetchone()[0]
            if pending_tldrs:
                if max_shards is not None and processed >= max_shards:
                    return _status(db, release_id)
                _create_corpus_candidate_map(db, release_id)
        for source_path, size, mtime_ns, complete in db.execute(
            """SELECT source_path,source_size,source_mtime_ns,complete FROM s2_shards
            WHERE release_id=? AND dataset=? ORDER BY source_path""",
            [release_id, dataset],
        ).fetchall():
            if complete:
                continue
            if max_shards is not None and processed >= max_shards:
                return _status(db, release_id)
            path = directory.joinpath(*source_path.split("/"))
            importer = (
                _import_paper_shard if dataset == "papers" else _import_tldr_shard
            )
            if dataset == "papers":
                importer(db, path, release_id, source_path, size, mtime_ns, batch_size)
            else:
                importer(
                    db,
                    path,
                    release_id,
                    tldr_model,
                    source_path,
                    size,
                    mtime_ns,
                    batch_size,
                )
            processed += 1
    return _status(db, release_id)


def apply_s2_projection(
    db: duckdb.DuckDBPyConnection,
    release_id: str,
    model: str | None = None,
) -> dict:
    """在完整 release 上裁决重复项并更新 arXiv JSON 与待发布集合。

    精确 arXiv 命中的 DOI 诊断不取消强身份匹配；事实冲突、CorpusId 多义和 TLDR 冲突不投影。
    """
    _create_schema(db)
    release = db.execute(
        "SELECT tldr_model FROM s2_releases WHERE release_id=?", [release_id]
    ).fetchone()
    if release is None:
        raise ValueError("S2 release has not been imported")
    tldr_model = release[0]
    if model is not None and model != tldr_model:
        raise ValueError("requested TLDR model differs from the imported release")
    incomplete = db.execute(
        "SELECT count(*) FROM s2_shards WHERE release_id=? AND NOT complete",
        [release_id],
    ).fetchone()[0]
    arxiv_state = db.execute(
        "SELECT complete FROM import_state WHERE singleton=true"
    ).fetchone()
    if incomplete or arxiv_state is None or not arxiv_state[0]:
        raise ValueError("arXiv, S2 papers, and S2 tldrs imports must reach EOF first")

    db.begin()
    try:
        # 冲突要等所有分片完成后统一裁决，不能由分片遍历顺序决定最终信号。
        db.execute(
            "DELETE FROM s2_diagnostics WHERE release_id=? AND reason IN ('paper_fact_conflict','corpus_id_conflict','tldr_conflict')",
            [release_id],
        )
        db.execute(
            """CREATE OR REPLACE TEMP TABLE _s2_paper_conflicts AS
            SELECT arxiv_id FROM s2_papers WHERE release_id=?
            GROUP BY arxiv_id
            HAVING count(DISTINCT to_json(struct_pack(
                corpus_id := corpus_id, doi := doi,
                citation_count := citation_count,
                influential_citation_count := influential_citation_count,
                is_open_access := is_open_access))) > 1""",
            [release_id],
        )
        db.execute(
            """CREATE OR REPLACE TEMP TABLE _s2_corpus_conflicts AS
            SELECT corpus_id FROM s2_papers
            WHERE release_id=? AND corpus_id IS NOT NULL
            GROUP BY corpus_id HAVING count(DISTINCT arxiv_id)>1""",
            [release_id],
        )
        db.execute(
            """INSERT OR IGNORE INTO s2_diagnostics
            SELECT p.release_id,'papers',p.source_path,p.source_line,
                'paper_fact_conflict',p.arxiv_id,
                to_json(struct_pack(arxiv_id := p.arxiv_id, corpus_id := p.corpus_id,
                    doi := p.doi, citation_count := p.citation_count,
                    influential_citation_count := p.influential_citation_count,
                    is_open_access := p.is_open_access))
            FROM s2_papers p JOIN _s2_paper_conflicts c USING(arxiv_id)
            WHERE p.release_id=?""",
            [release_id],
        )
        db.execute(
            """INSERT OR IGNORE INTO s2_diagnostics
            SELECT p.release_id,'papers',p.source_path,p.source_line,
                'corpus_id_conflict',p.corpus_id,
                to_json(struct_pack(corpus_id := p.corpus_id, arxiv_id := p.arxiv_id))
            FROM s2_papers p JOIN _s2_corpus_conflicts c USING(corpus_id)
            WHERE p.release_id=?""",
            [release_id],
        )
        db.execute(
            """
            CREATE OR REPLACE TEMP TABLE _s2_invalid_arxiv AS
            SELECT arxiv_id FROM _s2_paper_conflicts
            UNION
            SELECT DISTINCT p.arxiv_id FROM s2_papers p
            JOIN _s2_corpus_conflicts c USING(corpus_id)
            WHERE p.release_id=?;
        """,
            [release_id],
        )
        db.execute(
            """CREATE OR REPLACE TEMP TABLE _s2_tldr_conflicts AS
            SELECT corpus_id FROM s2_tldrs
            WHERE release_id=? AND model=?
            GROUP BY corpus_id
            HAVING count(DISTINCT to_json(struct_pack(text := text)))>1""",
            [release_id, tldr_model],
        )
        db.execute(
            """INSERT OR IGNORE INTO s2_diagnostics
            SELECT t.release_id,'tldrs',t.source_path,t.source_line,
                'tldr_conflict',t.corpus_id,
                to_json(struct_pack(corpus_id := t.corpus_id, model := t.model, text := t.text))
            FROM s2_tldrs t JOIN _s2_tldr_conflicts c USING(corpus_id)
            WHERE t.release_id=? AND t.model=?""",
            [release_id, tldr_model],
        )
        db.execute(
            """CREATE OR REPLACE TEMP TABLE _s2_canonical_papers AS
            SELECT p.release_id,p.arxiv_id,p.corpus_id,p.citation_count,
                p.influential_citation_count,p.is_open_access,p.source_path,p.source_line
            FROM s2_papers p
            WHERE p.release_id=?
                AND NOT EXISTS (SELECT 1 FROM _s2_invalid_arxiv bad WHERE bad.arxiv_id=p.arxiv_id)
            QUALIFY row_number() OVER (
                PARTITION BY p.arxiv_id ORDER BY p.source_path,p.source_line
            )=1""",
            [release_id],
        )
        db.execute(
            """CREATE OR REPLACE TEMP TABLE _s2_canonical_tldrs AS
            SELECT t.corpus_id,t.model,t.text,t.source_path,t.source_line
            FROM s2_tldrs t
            WHERE t.release_id=? AND t.model=?
                AND NOT EXISTS (SELECT 1 FROM _s2_tldr_conflicts bad
                    WHERE bad.corpus_id=t.corpus_id)
                AND EXISTS (SELECT 1 FROM _s2_canonical_papers p
                    WHERE p.corpus_id=t.corpus_id)
            QUALIFY row_number() OVER (
                PARTITION BY t.corpus_id ORDER BY t.source_path,t.source_line
            )=1""",
            [release_id, tldr_model],
        )
        db.execute(
            """CREATE OR REPLACE TEMP TABLE _s2_projection AS
            SELECT p.arxiv_id,
                to_json(struct_pack(
                    corpus_id := p.corpus_id,
                    citation_count := p.citation_count,
                    influential_citation_count := p.influential_citation_count,
                    is_open_access := p.is_open_access,
                    tldr := CASE WHEN t.source_line IS NULL THEN NULL::JSON
                        ELSE to_json(struct_pack(model := t.model, text := t.text)) END,
                    release := ?,
                    paper_source := to_json(struct_pack(
                        release := ?, path := p.source_path, line := p.source_line)),
                    tldr_source := CASE WHEN t.source_line IS NULL THEN NULL::JSON
                        ELSE to_json(struct_pack(
                            release := ?, path := t.source_path, line := t.source_line)) END
                )) AS s2_document
            FROM _s2_canonical_papers p
            LEFT JOIN _s2_canonical_tldrs t USING(corpus_id)""",
            [release_id, release_id, release_id],
        )
        db.execute("""
            CREATE OR REPLACE TEMP TABLE _s2_projection_targets AS
            SELECT arxiv_id FROM _s2_projection
        """)
        changed = _write_s2_document_changes(db)
        db.commit()
    except BaseException:
        db.rollback()
        raise

    return {
        "release_id": release_id,
        "projected": changed,
        "pending": db.execute("SELECT count(*) FROM pending_publish").fetchone()[0],
        "diagnostics": db.execute(
            "SELECT count(*) FROM s2_diagnostics WHERE release_id=?", [release_id]
        ).fetchone()[0],
    }


def _write_s2_document_changes(db: duckdb.DuckDBPyConnection) -> int:
    """按有序 ID 范围重建文档；调用方事务统一提交事实与待发布集合。"""
    db.execute("""
        CREATE OR REPLACE TEMP TABLE _s2_changed (
            arxiv_id VARCHAR,
            new_document JSON
        )
    """)
    # 全量 JSON UPDATE 的旧行版本会保留在事务 undo buffers；完整快照改用新表写入后原子换名。
    replacement_ranges = db.execute(
        """SELECT min(arxiv_id),max(arxiv_id)
        FROM (
            SELECT arxiv_id,
                (row_number() OVER (ORDER BY arxiv_id)-1) // ? AS batch_number
            FROM arxiv
        )
        GROUP BY batch_number ORDER BY batch_number""",
        [_PROJECTION_TARGET_BATCH_SIZE],
    ).fetchall()

    # 只保存每 5000 个有序 target 的边界；范围内非 target 仍由 SQL JOIN 排除。
    ranges = db.execute(
        """SELECT min(arxiv_id),max(arxiv_id)
        FROM (
            SELECT arxiv_id,
                (row_number() OVER (ORDER BY arxiv_id)-1) // ? AS batch_number
            FROM _s2_projection_targets
        )
        GROUP BY batch_number ORDER BY batch_number""",
        [_PROJECTION_TARGET_BATCH_SIZE],
    ).fetchall()

    staged_changed = 0
    for range_start, range_end in ranges:
        # 先筛掉 S2 JSON 未变化的论文，再展开并重建其余 metadata map。
        inserted = db.execute(
            """INSERT INTO _s2_changed
            WITH changed_targets AS MATERIALIZED (
                SELECT a.arxiv_id,a.document,p.s2_document
                FROM arxiv a
                JOIN _s2_projection_targets target USING(arxiv_id)
                LEFT JOIN _s2_projection p USING(arxiv_id)
                WHERE a.arxiv_id BETWEEN ? AND ?
                    AND json_extract(a.document,'$.metadata.s2')
                        IS DISTINCT FROM p.s2_document
            ), updated AS (
                SELECT c.arxiv_id,c.document,c.s2_document,
                (
                    SELECT map_from_entries(
                        list(struct_pack(key := d.key, value := d.value))
                    )
                    FROM json_each(c.document) d
                    WHERE d.key <> 'metadata'
                ) AS base_fields,
                coalesce(
                    (
                        SELECT map_from_entries(
                            list(struct_pack(key := m.key, value := m.value))
                        )
                        FROM json_each(
                            coalesce(json_extract(c.document,'$.metadata'),'{}'::JSON)
                        ) m
                        WHERE m.key <> 's2'
                    ),
                    map([]::VARCHAR[], []::JSON[])
                ) AS other_metadata
                FROM changed_targets c
            ), rebuilt AS (
                SELECT arxiv_id,
                    CASE
                        WHEN s2_document IS NULL AND len(map_keys(other_metadata))=0
                            THEN to_json(base_fields)
                        ELSE to_json(map_concat(
                            base_fields,
                            map(['metadata'], [to_json(
                                CASE WHEN s2_document IS NULL THEN other_metadata
                                    ELSE map_concat(other_metadata, map(['s2'], [s2_document]))
                                END
                            )])
                        ))
                    END AS new_document
                FROM updated
            )
            SELECT arxiv_id,new_document FROM rebuilt ORDER BY arxiv_id
            RETURNING arxiv_id""",
            [range_start, range_end],
        ).fetchall()
        staged_changed += len(inserted)
        print(
            orjson.dumps(
                {
                    "stage": "s2_projection",
                    "range_start": range_start,
                    "range_end": range_end,
                    "staged_changed": staged_changed,
                    "committed": False,
                }
            ).decode(),
            flush=True,
        )

    changed = staged_changed
    if changed:
        written = 0
        # INSERT 只追加到新表，不为旧 document 建立事务 undo；分批限制单条 INSERT 的工作集。
        db.execute(
            """CREATE TABLE _arxiv_s2_replacement (
                arxiv_id VARCHAR PRIMARY KEY,
                doi VARCHAR,
                source JSON,
                document JSON
            )"""
        )
        for range_start, range_end in replacement_ranges:
            inserted = db.execute(
                """INSERT INTO _arxiv_s2_replacement
                SELECT a.arxiv_id,a.doi,a.source,coalesce(c.new_document,a.document)
                FROM arxiv a
                LEFT JOIN (
                    SELECT arxiv_id,new_document FROM _s2_changed
                    WHERE arxiv_id BETWEEN ? AND ?
                ) c USING(arxiv_id)
                WHERE a.arxiv_id BETWEEN ? AND ?
                ORDER BY a.arxiv_id
                RETURNING arxiv_id""",
                [range_start, range_end, range_start, range_end],
            ).fetchall()
            written += len(inserted)
            print(
                orjson.dumps(
                    {
                        "stage": "s2_projection_write",
                        "range_start": range_start,
                        "range_end": range_end,
                        "staged_changed": changed,
                        "written": written,
                        "committed": False,
                    }
                ).decode(),
                flush=True,
            )
        db.execute("ALTER TABLE arxiv RENAME TO _arxiv_s2_previous")
        db.execute("ALTER TABLE _arxiv_s2_replacement RENAME TO arxiv")
        db.execute("DROP TABLE _arxiv_s2_previous")
        for range_start, range_end in ranges:
            db.execute(
                """INSERT OR IGNORE INTO pending_publish
                SELECT arxiv_id FROM _s2_changed
                WHERE arxiv_id BETWEEN ? AND ?""",
                [range_start, range_end],
            )
        db.execute("UPDATE import_state SET published=false WHERE singleton=true")
    return changed

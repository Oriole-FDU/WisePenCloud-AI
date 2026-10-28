"""S2 ETL 对真实 DuckDB 与 gzip 边界的最小行为合同。"""

import gzip
import os

import duckdb
import orjson
import pytest
from offline.arxiv import open_database
from offline.s2 import apply_s2_projection, import_s2


def _write_gzip(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with (
        path.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed,
    ):
        for record in records:
            compressed.write(orjson.dumps(record) + b"\n")
    return path


def _database(tmp_path, papers):
    db = open_database(tmp_path / "papers.duckdb")
    for arxiv_id, doi in papers:
        document = {
            "arxiv_id": arxiv_id,
            "title": f"Title for {arxiv_id}",
            "url": f"https://arxiv.org/abs/{arxiv_id}",
        }
        db.execute(
            "INSERT INTO arxiv VALUES (?, ?, ?::JSON, ?::JSON)",
            [arxiv_id, doi, "{}", orjson.dumps(document).decode()],
        )
    db.execute(
        "INSERT INTO import_state(singleton,byte_offset,records,complete,published) VALUES (true,0,?,true,true)",
        [len(papers)],
    )
    return db


def _paper(
    corpus_id, *, arxiv_id=None, doi=None, citations=0, influential=0, open_access=False
):
    externalids = {}
    if arxiv_id is not None:
        externalids["ArXiv"] = arxiv_id
    if doi is not None:
        externalids["DOI"] = doi
    return {
        "corpusid": corpus_id,
        "externalids": externalids,
        "citationcount": citations,
        "influentialcitationcount": influential,
        "isopenaccess": open_access,
    }


def test_import_matches_arxiv_and_unique_doi_and_projects_selected_tldr(
    tmp_path, capsys
):
    db = _database(
        tmp_path,
        [
            ("2201.00001", "10.1111/a"),
            ("2202.00002", "10.5555/b"),
            ("2203.00003", "10.9999/dup"),
            ("2204.00004", "10.9999/dup"),
            ("2205.00005", None),
        ],
    )
    papers_dir = tmp_path / "papers"
    tldrs_dir = tmp_path / "tldrs"
    _write_gzip(
        papers_dir / "part-0001.gz",
        [
            _paper(
                1001,
                arxiv_id="https://arxiv.org/abs/2201.00001v2",
                doi="10.1111/a",
                citations=0,
                influential=0,
                open_access=False,
            ),
            _paper(1002, doi="https://doi.org/10.5555/b", citations=7),
            _paper(1003, arxiv_id="9999.99999", citations=4),
            _paper(1004, doi="10.9999/dup", citations=5),
            _paper(1005, arxiv_id="2205.00005", citations=2),
        ],
    )
    _write_gzip(
        tldrs_dir / "part-0001.gz",
        [
            {"corpusid": 1001, "model": "tldr@v2.0.0", "text": ""},
            {"corpusid": 1002, "model": "tldr@v2.0.0", "text": "A summary"},
            {"corpusid": 9999, "model": "tldr@v2.0.0", "text": "unmatched"},
            {"corpusid": 1001, "model": "other-model", "text": "ignored"},
        ],
    )

    try:
        status = import_s2(
            db,
            papers_dir,
            tldrs_dir,
            release_id="declared-test-release",
            batch_size=1,
        )
        progress = [
            orjson.loads(line)
            for line in capsys.readouterr().out.splitlines()
            if line.strip()
        ]
        assert progress[0]["release"] == "declared-test-release"
        assert progress[0]["dataset"] == "papers"
        assert progress[0]["source_path"] == "part-0001.gz"
        assert progress[0]["complete"] is False
        assert progress[-1]["dataset"] == "tldrs"
        assert progress[-1]["scanned"] == 4
        assert progress[-1]["retained"] == 2
        assert progress[-1]["complete"] is True
        assert status["complete"] is True
        assert status["papers"]["scanned"] == 5
        assert status["papers"]["retained"] == 3
        assert status["papers"]["matched"] == 3
        assert status["papers"]["ambiguous"] == 1
        assert status["papers"]["conflicts"] == 0
        assert status["papers"]["missing"] == 1
        assert status["tldrs"]["scanned"] == 4
        assert status["tldrs"]["retained"] == 2
        assert status["tldrs"]["matched"] == 2
        assert status["tldrs"]["missing"] == 1
        assert status["tldrs"]["filtered_model"] == 1

        applied = apply_s2_projection(db, "declared-test-release")
        assert applied["projected"] == 3
        assert db.execute(
            "SELECT arxiv_id FROM pending_publish ORDER BY arxiv_id"
        ).fetchall() == [("2201.00001",), ("2202.00002",), ("2205.00005",)]
        assert db.execute("SELECT published FROM import_state").fetchone()[0] is False

        documents = {
            arxiv_id: orjson.loads(document)
            for arxiv_id, document in db.execute(
                "SELECT arxiv_id,document FROM arxiv ORDER BY arxiv_id"
            ).fetchall()
        }
        assert documents["2201.00001"]["title"] == "Title for 2201.00001"
        s2 = documents["2201.00001"]["metadata"]["s2"]
        assert s2["corpus_id"] == "1001"
        assert s2["citation_count"] == 0
        assert s2["influential_citation_count"] == 0
        assert s2["is_open_access"] is False
        assert s2["tldr"] == {"model": "tldr@v2.0.0", "text": ""}
        assert s2["release"] == "declared-test-release"
        assert s2["paper_source"] == {
            "release": "declared-test-release",
            "path": "part-0001.gz",
            "line": 1,
        }
        assert s2["tldr_source"]["path"] == "part-0001.gz"
        assert s2["tldr_source"]["line"] == 1
        assert documents["2202.00002"]["metadata"]["s2"]["tldr"]["text"] == "A summary"
        assert documents["2205.00005"]["metadata"]["s2"]["tldr"] is None
        assert documents["2205.00005"]["metadata"]["s2"]["tldr_source"] is None
        assert "metadata" not in documents["2203.00003"]

        repeated_status = import_s2(
            db,
            papers_dir,
            tldrs_dir,
            release_id="declared-test-release",
            batch_size=1,
        )
        assert repeated_status == status
        assert apply_s2_projection(db, "declared-test-release")["projected"] == 0
        assert db.execute("SELECT count(*) FROM pending_publish").fetchone()[0] == 3
    finally:
        db.close()


def test_doi_ambiguity_and_arxiv_doi_conflict_do_not_choose_a_fallback(tmp_path):
    db = _database(
        tmp_path,
        [
            ("2301.00001", "10.1000/duplicate"),
            ("2302.00002", "10.1000/duplicate"),
            ("2303.00003", "10.1000/right"),
            ("2304.00004", "10.1000/other"),
        ],
    )
    papers_dir = tmp_path / "papers"
    tldrs_dir = tmp_path / "tldrs"
    tldrs_dir.mkdir()
    _write_gzip(
        papers_dir / "part.gz",
        [
            _paper(2001, doi="10.1000/duplicate"),
            _paper(2002, arxiv_id="2999.00001", doi="10.1000/right"),
            _paper(2003, arxiv_id="2303.00003", doi="10.1000/other", citations=9),
        ],
    )

    try:
        status = import_s2(
            db, papers_dir, tldrs_dir, release_id="identity-test", batch_size=1
        )
        assert status["papers"]["retained"] == 1
        assert status["papers"]["ambiguous"] == 1
        assert status["papers"]["conflicts"] == 2
        assert db.execute(
            "SELECT arxiv_id FROM s2_papers WHERE release_id='identity-test'"
        ).fetchall() == [("2303.00003",)]
        reasons = {
            row[0]
            for row in db.execute(
                "SELECT DISTINCT reason FROM s2_diagnostics WHERE release_id='identity-test'"
            ).fetchall()
        }
        assert reasons == {"doi_ambiguous", "arxiv_doi_conflict"}

        apply_s2_projection(db, "identity-test")
        assert (
            db.execute(
                "SELECT json_extract_string(document,'$.metadata.s2.citation_count') FROM arxiv WHERE arxiv_id='2303.00003'"
            ).fetchone()[0]
            == "9"
        )
        assert (
            db.execute(
                "SELECT json_extract(document,'$.metadata.s2') FROM arxiv WHERE arxiv_id='2304.00004'"
            ).fetchone()[0]
            is None
        )
    finally:
        db.close()


def test_identical_duplicate_facts_and_tldrs_deduplicate_deterministically(tmp_path):
    db = _database(tmp_path, [("2401.00001", "10.2400/one")])
    papers_dir = tmp_path / "papers"
    tldrs_dir = tmp_path / "tldrs"
    duplicate = _paper(
        3001,
        arxiv_id="2401.00001",
        doi="10.2400/one",
        citations=12,
        influential=2,
        open_access=False,
    )
    _write_gzip(papers_dir / "part.gz", [duplicate, duplicate])
    summary = {"corpusid": 3001, "model": "tldr@v2.0.0", "text": ""}
    _write_gzip(tldrs_dir / "part.gz", [summary, summary])

    try:
        first = import_s2(
            db, papers_dir, tldrs_dir, release_id="dedupe-test", batch_size=1
        )
        assert first["papers"]["retained"] == 2
        assert first["tldrs"]["retained"] == 2
        applied = apply_s2_projection(db, "dedupe-test")
        assert applied["projected"] == 1
        document = orjson.loads(
            db.execute(
                "SELECT document FROM arxiv WHERE arxiv_id='2401.00001'"
            ).fetchone()[0]
        )
        assert document["metadata"]["s2"]["paper_source"]["line"] == 1
        assert document["metadata"]["s2"]["tldr"]["text"] == ""
        assert applied["diagnostics"] == 0

        assert (
            import_s2(db, papers_dir, tldrs_dir, release_id="dedupe-test", batch_size=1)
            == first
        )
        assert apply_s2_projection(db, "dedupe-test")["projected"] == 0
        assert db.execute("SELECT count(*) FROM pending_publish").fetchone()[0] == 1
    finally:
        db.close()


def test_conflicting_paper_and_tldr_facts_are_diagnosed_and_not_projected(tmp_path):
    db = _database(
        tmp_path,
        [("2501.00001", "10.2500/one"), ("2502.00002", "10.2500/two")],
    )
    papers_dir = tmp_path / "papers"
    tldrs_dir = tmp_path / "tldrs"
    base = _paper(4001, arxiv_id="2501.00001", doi="10.2500/one", citations=3)
    conflicting = {**base, "citationcount": 4}
    second_corpus_id = _paper(
        4002, arxiv_id="2501.00001", doi="10.2500/one", citations=3
    )
    corpus_reused = _paper(4001, arxiv_id="2502.00002", doi="10.2500/two", citations=3)
    _write_gzip(
        papers_dir / "part.gz", [base, conflicting, second_corpus_id, corpus_reused]
    )
    _write_gzip(
        tldrs_dir / "part.gz",
        [
            {"corpusid": 4001, "model": "tldr@v2.0.0", "text": "first"},
            {"corpusid": 4001, "model": "tldr@v2.0.0", "text": "second"},
        ],
    )

    try:
        status = import_s2(db, papers_dir, tldrs_dir, release_id="conflict-test")
        assert status["complete"] is True
        applied = apply_s2_projection(db, "conflict-test")
        assert applied["projected"] == 0
        reasons = {
            row[0]
            for row in db.execute(
                "SELECT DISTINCT reason FROM s2_diagnostics WHERE release_id='conflict-test'"
            ).fetchall()
        }
        assert reasons == {
            "paper_fact_conflict",
            "corpus_id_conflict",
            "corpus_id_ambiguous",
            "tldr_conflict",
        }
        documents = {
            arxiv_id: orjson.loads(document)
            for arxiv_id, document in db.execute(
                "SELECT arxiv_id,document FROM arxiv WHERE arxiv_id IN ('2501.00001','2502.00002')"
            ).fetchall()
        }
        assert "metadata" not in documents["2501.00001"]
        assert "metadata" not in documents["2502.00002"]
        assert db.execute("SELECT count(*) FROM pending_publish").fetchone()[0] == 0
    finally:
        db.close()


def test_corrupt_gzip_rolls_back_shard_and_retries_from_its_start(tmp_path):
    db = _database(tmp_path, [("2601.00001", "10.2600/one")])
    papers_dir = tmp_path / "papers"
    tldrs_dir = tmp_path / "tldrs"
    tldrs_dir.mkdir()
    path = _write_gzip(
        papers_dir / "part.gz",
        [_paper(5001, arxiv_id="2601.00001", doi="10.2600/one", citations=1)],
    )
    good = path.read_bytes()
    stat = path.stat()
    path.write_bytes(good[:-1] + bytes([good[-1] ^ 1]))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    try:
        with pytest.raises(OSError):
            import_s2(db, papers_dir, tldrs_dir, release_id="recovery-test")
        assert (
            db.execute(
                "SELECT complete FROM s2_shards WHERE release_id='recovery-test'"
            ).fetchone()[0]
            is False
        )
        assert (
            db.execute(
                "SELECT count(*) FROM s2_papers WHERE release_id='recovery-test'"
            ).fetchone()[0]
            == 0
        )
        with pytest.raises(ValueError):
            apply_s2_projection(db, "recovery-test")

        path.write_bytes(good)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        resumed = import_s2(db, papers_dir, tldrs_dir, release_id="recovery-test")
        assert resumed["complete"] is True
        assert resumed["papers"]["retained"] == 1
        assert (
            db.execute(
                "SELECT count(*) FROM s2_papers WHERE release_id='recovery-test'"
            ).fetchone()[0]
            == 1
        )
    finally:
        db.close()


def test_resume_uses_full_shard_inventory_and_rejects_added_files(tmp_path):
    db = _database(
        tmp_path,
        [("2701.00001", "10.2700/one"), ("2702.00002", "10.2700/two")],
    )
    papers_dir = tmp_path / "papers"
    tldrs_dir = tmp_path / "tldrs"
    tldrs_dir.mkdir()
    _write_gzip(
        papers_dir / "a.gz",
        [_paper(6001, arxiv_id="2701.00001", doi="10.2700/one")],
    )
    _write_gzip(
        papers_dir / "b.gz",
        [_paper(6002, arxiv_id="2702.00002", doi="10.2700/two")],
    )

    try:
        partial = import_s2(
            db, papers_dir, tldrs_dir, release_id="manifest-test", max_shards=1
        )
        assert partial["complete"] is False
        assert partial["completed_shards"] == 1
        complete = import_s2(db, papers_dir, tldrs_dir, release_id="manifest-test")
        assert complete["complete"] is True
        assert complete["completed_shards"] == 2
        assert (
            import_s2(db, papers_dir, tldrs_dir, release_id="manifest-test") == complete
        )

        _write_gzip(
            papers_dir / "c.gz",
            [_paper(6003, arxiv_id="2701.00001", doi="10.2700/one")],
        )
        with pytest.raises(ValueError, match="inventory"):
            import_s2(db, papers_dir, tldrs_dir, release_id="manifest-test")
    finally:
        db.close()


def test_projection_preserves_other_metadata_and_explicit_nulls(tmp_path, capsys):
    db = _database(tmp_path, [("2801.00001", "10.2800/one")])
    papers_dir = tmp_path / "papers"
    tldrs_dir = tmp_path / "tldrs"
    _write_gzip(
        papers_dir / "part.gz",
        [_paper(7001, arxiv_id="2801.00001", doi="10.2800/one", citations=0)],
    )
    tldrs_dir.mkdir()
    old_document = orjson.loads(
        db.execute("SELECT document FROM arxiv WHERE arxiv_id='2801.00001'").fetchone()[
            0
        ]
    )
    old_document["metadata"] = {
        "s2": {"release": "old-release", "tldr": {"model": "old", "text": "old"}},
        "other": {"kept": True, "nullable": None},
    }
    db.execute(
        "UPDATE arxiv SET document=?::JSON WHERE arxiv_id='2801.00001'",
        [orjson.dumps(old_document).decode()],
    )

    try:
        import_s2(db, papers_dir, tldrs_dir, release_id="replace-test")
        capsys.readouterr()
        first = apply_s2_projection(db, "replace-test")
        progress = [
            orjson.loads(line)
            for line in capsys.readouterr().out.splitlines()
            if line.strip()
        ]
        document = orjson.loads(
            db.execute(
                "SELECT document FROM arxiv WHERE arxiv_id='2801.00001'"
            ).fetchone()[0]
        )
        assert db.execute(
            "SELECT doi,source FROM arxiv WHERE arxiv_id='2801.00001'"
        ).fetchone() == ("10.2800/one", "{}")
        assert document["metadata"]["other"] == {
            "kept": True,
            "nullable": None,
        }
        s2 = document["metadata"]["s2"]
        assert s2["release"] == "replace-test"
        assert "tldr" in s2 and s2["tldr"] is None
        assert "tldr_source" in s2 and s2["tldr_source"] is None

        assert first["projected"] == 1
        staging_events = [e for e in progress if e["stage"] == "s2_projection"]
        write_events = [e for e in progress if e["stage"] == "s2_projection_write"]
        assert staging_events
        assert staging_events[-1]["staged_changed"] == 1
        assert write_events
        assert write_events[-1]["written"] == 1
        assert all(event["committed"] is False for event in progress)
        with pytest.raises(duckdb.ConstraintException):
            db.execute(
                "INSERT INTO arxiv VALUES ('2801.00001','10.2800/one','{}'::JSON,'{}'::JSON)"
            )
        assert apply_s2_projection(db, "replace-test")["projected"] == 0
        assert db.execute("SELECT count(*) FROM pending_publish").fetchone()[0] == 1
    finally:
        db.close()


def test_input_directories_must_exist_and_papers_must_have_a_shard(tmp_path):
    db = _database(tmp_path, [])
    missing_papers = tmp_path / "missing-papers"
    tldrs_dir = tmp_path / "tldrs"
    tldrs_dir.mkdir()
    empty_papers = tmp_path / "empty-papers"
    empty_papers.mkdir()
    try:
        with pytest.raises(FileNotFoundError):
            import_s2(db, missing_papers, tldrs_dir, release_id="missing-input")
        with pytest.raises(ValueError, match="at least one gzip shard"):
            import_s2(db, empty_papers, tldrs_dir, release_id="empty-input")
    finally:
        db.close()

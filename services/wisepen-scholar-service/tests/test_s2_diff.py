"""S2 release 差分的持久化、重放与签名文件协议行为。"""

import copy
import gzip
import io
from collections import Counter

import httpx
import orjson
import pytest

from scholar.domain.models.identifiers import IdentifierKind
from scholar.etl.arxiv import open_database
from scholar.etl.s2 import apply_s2_projection, import_s2
from scholar.etl.s2_diff import sync_s2_diffs


def _gzip_jsonl(records):
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed:
        for record in records:
            compressed.write(orjson.dumps(record) + b"\n")
    return output.getvalue()


def _paper(corpus_id, arxiv_id, doi, citations, open_access=True):
    return {
        "corpusid": corpus_id,
        "externalids": {"ArXiv": arxiv_id, "DOI": doi},
        "citationcount": citations,
        "influentialcitationcount": None,
        "isopenaccess": open_access,
    }


def _base_database(tmp_path):
    db = open_database(tmp_path / "sample.duckdb")
    papers = [
        ("2201.00001", "10.2201/one"),
        ("2202.00002", "10.2202/two"),
        ("2203.00003", "10.2203/three"),
    ]
    for arxiv_id, doi in papers:
        document = {
            "arxiv_id": arxiv_id,
            "title": f"Title {arxiv_id}",
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "metadata": {"other": {"keep": True, "nullable": None}},
        }
        db.execute(
            "INSERT INTO arxiv VALUES (?, ?, ?::JSON, ?::JSON)",
            [arxiv_id, doi, "{}", orjson.dumps(document).decode()],
        )
    db.execute(
        """INSERT INTO import_state(
            singleton,byte_offset,records,complete,published
        ) VALUES (true,0,3,true,true)"""
    )

    papers_dir, tldrs_dir = tmp_path / "base-papers", tmp_path / "base-tldrs"
    papers_dir.mkdir()
    tldrs_dir.mkdir()
    (papers_dir / "papers.gz").write_bytes(
        _gzip_jsonl(
            [
                _paper(11, "2201.00001", "10.2201/one", 1),
                _paper(22, "2202.00002", "10.2202/two", 2),
                _paper(33, "2203.00003", "10.2203/three", 3),
            ]
        )
    )
    (tldrs_dir / "tldrs.gz").write_bytes(
        _gzip_jsonl(
            [
                {"corpusid": 11, "model": "tldr@v2.0.0", "text": "old one"},
                {"corpusid": 22, "model": "tldr@v2.0.0", "text": "old two"},
                {"corpusid": 33, "model": "tldr@v2.0.0", "text": "old three"},
            ]
        )
    )
    import_s2(db, papers_dir, tldrs_dir, release_id="A")
    apply_s2_projection(db, "A")
    db.execute("DELETE FROM pending_publish")
    db.execute("UPDATE import_state SET published=true")
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline_state", ["unpublished", "pending", "pending_delete"])
async def test_diff_seed_requires_a_fully_published_baseline(tmp_path, baseline_state):
    db = _base_database(tmp_path)
    if baseline_state == "unpublished":
        db.execute("UPDATE import_state SET published=false")
    elif baseline_state == "pending":
        db.execute("INSERT INTO pending_publish VALUES ('2201.00001')")
    else:
        db.execute("INSERT INTO pending_delete VALUES ('2201.00001', '2026-10-27')")

    client = _FakeDatasetsClient({})
    http = _http({})

    async def publish():
        raise AssertionError("an incomplete baseline must not start diff sync")

    async def invalidate(_identifiers):
        raise AssertionError("an incomplete baseline must not invalidate aliases")

    try:
        with pytest.raises(ValueError, match="fully published"):
            await sync_s2_diffs(
                db,
                client,
                http,
                tmp_path / "diff-work",
                publish_pending=publish,
                invalidate_aliases=invalidate,
                base_release="A",
                target_release="A",
            )
        assert db.execute("SELECT count(*) FROM s2_diff_sync_state").fetchone()[0] == 0
    finally:
        await http.aclose()
        db.close()


class _FakeDatasetsClient:
    def __init__(self, edges, refreshed_urls=None, latest="C"):
        self.edges = edges
        self.refreshed_urls = refreshed_urls or {}
        self.latest = latest
        self.manifest_counts = Counter()
        self.calls = []
        self.release_calls = []

    async def release(self, release_id):
        self.release_calls.append(release_id)
        assert release_id == "latest"
        return {"release_id": self.latest}

    async def diffs(self, start_release, end_release, dataset):
        self.calls.append((start_release, end_release, dataset))
        all_edges = self.edges[dataset]
        start_index = next(
            index
            for index, edge in enumerate(all_edges)
            if edge["from_release"] == start_release
        )
        selected = []
        for edge in all_edges[start_index:]:
            selected.append(copy.deepcopy(edge))
            if edge["to_release"] == end_release:
                break
        if not selected or selected[-1]["to_release"] != end_release:
            raise AssertionError("fake manifest does not contain the requested range")

        for edge in selected:
            for action in ("update", "delete"):
                urls = edge[f"{action}_files"]
                identity = (edge["from_release"], edge["to_release"], dataset)
                self.manifest_counts[identity] += 1
                if self.manifest_counts[identity] > 1:
                    edge[f"{action}_files"] = [
                        self.refreshed_urls.get(url, url) for url in urls
                    ]
        return {
            "dataset": dataset,
            "start_release": start_release,
            "end_release": end_release,
            "diffs": selected,
        }


def _edge(from_release, to_release, *, update=(), delete=()):
    return {
        "from_release": from_release,
        "to_release": to_release,
        "update_files": list(update),
        "delete_files": list(delete),
    }


def _url(path, signature="fresh"):
    return f"https://objects.test{path}?sig={signature}"


def _manifest(paper_edges, tldr_edges):
    return {"papers": paper_edges, "tldrs": tldr_edges}


def _http(payloads, *, expired=(), observed=None):
    expired = set(expired)

    async def handle(request):
        if observed is not None:
            observed.append(request)
        assert "x-api-key" not in request.headers
        if request.url.path in expired and request.url.params.get("sig") == "expired":
            return httpx.Response(403, request=request)
        return httpx.Response(200, content=payloads[request.url.path], request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle))


@pytest.mark.asyncio
async def test_diff_chain_replaces_local_facts_and_clears_deleted_associations(
    tmp_path, capsys
):
    db = _base_database(tmp_path)
    old_paper_delete = _url("/p/ab/delete.gz", "expired")
    fresh_paper_delete = _url("/p/ab/delete.gz", "refreshed")
    paper_ab_update = _url("/p/ab/update.gz")
    tldr_ab_delete = _url("/t/ab/delete.gz")
    tldr_ab_update = _url("/t/ab/update.gz")
    paper_bc_update = _url("/p/bc/update.gz")
    tldr_bc_update = _url("/t/bc/update.gz")
    edges = _manifest(
        [
            _edge(
                "A",
                "B",
                update=[paper_ab_update],
                delete=[old_paper_delete],
            ),
            _edge("B", "C", update=[paper_bc_update]),
        ],
        [
            _edge("A", "B", update=[tldr_ab_update], delete=[tldr_ab_delete]),
            _edge("B", "C", update=[tldr_bc_update]),
        ],
    )
    client = _FakeDatasetsClient(
        edges, refreshed_urls={old_paper_delete: fresh_paper_delete}
    )
    payloads = {
        "/p/ab/delete.gz": _gzip_jsonl([{"corpusid": 33}]),
        "/p/ab/update.gz": _gzip_jsonl(
            [
                _paper(11, "2201.00001", "10.2201/one", 0, False),
                _paper(22, "2202.00002", "10.2202/two", 8, True),
            ]
        ),
        "/t/ab/delete.gz": _gzip_jsonl([{"corpusid": 22}]),
        "/t/ab/update.gz": _gzip_jsonl(
            [{"corpusid": 11, "model": "tldr@v2.0.0", "text": "summary B"}]
        ),
        "/p/bc/update.gz": _gzip_jsonl(
            [_paper(22, "2203.00003", "10.2203/three", 4, False)]
        ),
        "/t/bc/update.gz": _gzip_jsonl(
            [
                {"corpusid": 11, "model": "tldr@v2.0.0", "text": ""},
                {"corpusid": 22, "model": "tldr@v2.0.0", "text": None},
            ]
        ),
    }
    requests = []
    publish_calls = []
    invalidations = []

    async def publish():
        publish_calls.append(True)
        db.execute("DELETE FROM pending_publish")
        db.execute("UPDATE import_state SET published=true")
        from scholar.etl.arxiv import database_status

        return database_status(db)

    async def invalidate(identifiers):
        invalidations.append(identifiers)

    http = _http(payloads, expired={"/p/ab/delete.gz"}, observed=requests)
    try:
        status = await sync_s2_diffs(
            db,
            client,
            http,
            tmp_path / "diff-work",
            publish_pending=publish,
            invalidate_aliases=invalidate,
            base_release="A",
        )
        assert status["complete"] is True
        assert status["completed_release"] == "C"
        assert status["processed_files"] == 6
        assert client.release_calls == ["latest"]
        assert publish_calls == [True]
        assert len(invalidations) == 1
        assert all("x-api-key" not in request.headers for request in requests)
        assert "sig=" not in capsys.readouterr().out
        assert all(
            "?" not in row[0]
            for row in db.execute("SELECT object_path FROM s2_diff_files").fetchall()
        )

        documents = {
            arxiv_id: orjson.loads(document)
            for arxiv_id, document in db.execute(
                "SELECT arxiv_id,document FROM arxiv ORDER BY arxiv_id"
            ).fetchall()
        }
        assert set(documents) == {"2201.00001", "2202.00002", "2203.00003"}
        assert documents["2201.00001"]["metadata"]["other"] == {
            "keep": True,
            "nullable": None,
        }
        one = documents["2201.00001"]["metadata"]["s2"]
        assert one["release"] == "C"
        assert one["citation_count"] == 0
        assert one["is_open_access"] is False
        assert one["tldr"] == {"model": "tldr@v2.0.0", "text": ""}
        assert one["paper_source"]["release"] == "B"
        assert one["tldr_source"]["release"] == "C"

        assert "s2" not in documents["2202.00002"]["metadata"]
        assert documents["2202.00002"]["metadata"]["other"]["keep"] is True
        three = documents["2203.00003"]["metadata"]["s2"]
        assert three["release"] == "C"
        assert three["corpus_id"] == "22"
        assert three["citation_count"] == 4
        assert three["tldr"] == {"model": "tldr@v2.0.0", "text": None}
        assert three["paper_source"]["release"] == "C"
        assert (
            db.execute("SELECT count(*) FROM s2_diff_current_papers").fetchone()[0] == 2
        )
        assert db.execute("SELECT count(*) FROM pending_publish").fetchone()[0] == 0
        assert db.execute(
            "SELECT completed_release,target_release FROM s2_diff_sync_state"
        ).fetchone() == ("C", None)
        alias_kinds = {item.kind for item in invalidations[0]}
        assert {
            IdentifierKind.ARXIV,
            IdentifierKind.CORPUS_ID,
            IdentifierKind.DOI,
        } <= alias_kinds

        calls_before = len(client.calls)
        publish_calls.clear()
        invalidations.clear()
        noop = await sync_s2_diffs(
            db,
            client,
            http,
            tmp_path / "diff-work",
            publish_pending=publish,
            invalidate_aliases=invalidate,
            target_release="C",
        )
        assert noop["complete"] is True
        assert noop["processed_files"] == 0
        assert len(client.calls) == calls_before
        assert publish_calls == [True]
        assert invalidations == []
    finally:
        await http.aclose()
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("publish_status", "expected_complete"),
    [
        ({"pending": 1, "import": {"published": False}}, False),
        (
            {"pending": 0, "pending_delete": 1, "import": {"published": True}},
            False,
        ),
        ({"pending": 0, "import": {"published": False}}, False),
        ({"pending": 0, "import": {"published": True}}, True),
    ],
)
async def test_noop_release_confirms_publication_before_reporting_complete(
    tmp_path, publish_status, expected_complete
):
    db = _base_database(tmp_path)
    client = _FakeDatasetsClient({})
    http = _http({})
    publish_calls = []
    invalidations = []

    async def publish():
        publish_calls.append(True)
        return publish_status

    async def invalidate(identifiers):
        invalidations.append(identifiers)

    try:
        result = await sync_s2_diffs(
            db,
            client,
            http,
            tmp_path / "diff-work",
            publish_pending=publish,
            invalidate_aliases=invalidate,
            base_release="A",
            target_release="A",
        )
        assert result["complete"] is expected_complete
        assert result["processed_files"] == 0
        assert result["publish"] == publish_status
        assert publish_calls == [True]
        assert invalidations == []
        assert db.execute(
            "SELECT completed_release,target_release FROM s2_diff_sync_state"
        ).fetchone() == ("A", None)
    finally:
        await http.aclose()
        db.close()


@pytest.mark.asyncio
async def test_completed_files_and_frozen_target_replay_after_partial_publish(tmp_path):
    db = _base_database(tmp_path)
    paper_update = _url("/p/ab/update.gz")
    edges = _manifest(
        [_edge("A", "B", update=[paper_update])],
        [_edge("A", "B")],
    )
    client = _FakeDatasetsClient(edges)
    payloads = {
        "/p/ab/update.gz": _gzip_jsonl([_paper(11, "2201.00001", "10.2201/one", 7)])
    }
    http = _http(payloads)
    publish_attempts = 0
    invalidations = []

    async def publish():
        nonlocal publish_attempts
        publish_attempts += 1
        if publish_attempts == 1:
            return {"pending": 1, "import": {"published": False}}
        db.execute("DELETE FROM pending_publish")
        db.execute("UPDATE import_state SET published=true")
        from scholar.etl.arxiv import database_status

        return database_status(db)

    async def invalidate(identifiers):
        invalidations.append(identifiers)

    try:
        first = await sync_s2_diffs(
            db,
            client,
            http,
            tmp_path / "diff-work",
            publish_pending=publish,
            invalidate_aliases=invalidate,
            base_release="A",
            target_release="B",
        )
        assert first["complete"] is False
        assert first["completed_release"] == "A"
        assert first["target_release"] == "B"
        assert (
            db.execute(
                "SELECT complete FROM s2_diff_files WHERE dataset='papers'"
            ).fetchone()[0]
            is True
        )
        assert db.execute(
            "SELECT completed_release,target_release FROM s2_diff_sync_state"
        ).fetchone() == ("A", "B")

        second = await sync_s2_diffs(
            db,
            client,
            http,
            tmp_path / "diff-work",
            publish_pending=publish,
            invalidate_aliases=invalidate,
        )
        assert second["complete"] is True
        assert second["processed_files"] == 0
        assert publish_attempts == 2
        assert client.release_calls == []
        assert len(invalidations) == 1
        assert db.execute(
            "SELECT completed_release,target_release FROM s2_diff_sync_state"
        ).fetchone() == ("B", None)
    finally:
        await http.aclose()
        db.close()


@pytest.mark.asyncio
async def test_corrupt_gzip_file_rolls_back_and_replays_from_start(tmp_path):
    db = _base_database(tmp_path)
    paper_update = _url("/p/ab/update.gz")
    edges = _manifest(
        [_edge("A", "B", update=[paper_update])],
        [_edge("A", "B")],
    )
    client = _FakeDatasetsClient(edges)
    good = _gzip_jsonl([_paper(11, "2201.00001", "10.2201/one", 9)])
    payloads = {"/p/ab/update.gz": good[:-2]}
    http = _http(payloads)

    async def should_not_publish():
        raise AssertionError("a failed file must not reach publication")

    async def should_not_invalidate(_):
        raise AssertionError("a failed file must not invalidate aliases")

    try:
        with pytest.raises(EOFError):
            await sync_s2_diffs(
                db,
                client,
                http,
                tmp_path / "diff-work",
                publish_pending=should_not_publish,
                invalidate_aliases=should_not_invalidate,
                base_release="A",
                target_release="B",
            )
        assert (
            db.execute(
                "SELECT complete FROM s2_diff_files WHERE dataset='papers'"
            ).fetchone()[0]
            is False
        )
        assert (
            db.execute(
                "SELECT citation_count FROM s2_diff_current_papers WHERE corpus_id='11'"
            ).fetchone()[0]
            == 1
        )
        assert db.execute(
            "SELECT completed_release,target_release FROM s2_diff_sync_state"
        ).fetchone() == ("A", "B")

        payloads["/p/ab/update.gz"] = good

        async def publish():
            db.execute("DELETE FROM pending_publish")
            db.execute("UPDATE import_state SET published=true")
            from scholar.etl.arxiv import database_status

            return database_status(db)

        async def invalidate(_):
            return None

        recovered = await sync_s2_diffs(
            db,
            client,
            http,
            tmp_path / "diff-work",
            publish_pending=publish,
            invalidate_aliases=invalidate,
        )
        assert recovered["complete"] is True
        assert (
            db.execute(
                "SELECT citation_count FROM s2_diff_current_papers WHERE corpus_id='11'"
            ).fetchone()[0]
            == 9
        )
        assert (
            db.execute(
                "SELECT complete FROM s2_diff_files WHERE dataset='papers'"
            ).fetchone()[0]
            is True
        )
    finally:
        await http.aclose()
        db.close()


@pytest.mark.asyncio
async def test_ambiguous_arxiv_current_links_are_not_arbitrarily_projected(tmp_path):
    db = _base_database(tmp_path)
    update_url = _url("/p/ab/update.gz")
    edges = _manifest(
        [_edge("A", "B", update=[update_url])],
        [_edge("A", "B")],
    )
    client = _FakeDatasetsClient(edges)
    payloads = {
        "/p/ab/update.gz": _gzip_jsonl(
            [
                _paper(44, "2201.00001", "10.2201/one", 4),
                _paper(45, "2201.00001", "10.2201/one", 5),
            ]
        )
    }
    http = _http(payloads)

    async def publish():
        db.execute("DELETE FROM pending_publish")
        db.execute("UPDATE import_state SET published=true")
        from scholar.etl.arxiv import database_status

        return database_status(db)

    async def invalidate(_):
        return None

    try:
        status = await sync_s2_diffs(
            db,
            client,
            http,
            tmp_path / "diff-work",
            publish_pending=publish,
            invalidate_aliases=invalidate,
            base_release="A",
            target_release="B",
        )
        assert status["complete"] is True
        doc = orjson.loads(
            db.execute(
                "SELECT document FROM arxiv WHERE arxiv_id='2201.00001'"
            ).fetchone()[0]
        )
        assert "s2" not in doc["metadata"]
        reasons = {
            row[0]
            for row in db.execute(
                "SELECT DISTINCT reason FROM s2_diff_diagnostics"
            ).fetchall()
        }
        assert "arxiv_corpus_ambiguous" in reasons
        assert (
            db.execute(
                "SELECT count(*) FROM s2_diff_current_papers WHERE arxiv_id='2201.00001'"
            ).fetchone()[0]
            == 2
        )
    finally:
        await http.aclose()
        db.close()


@pytest.mark.asyncio
async def test_conflicting_updates_for_one_corpus_id_remove_the_old_signal(tmp_path):
    db = _base_database(tmp_path)
    update_url = _url("/p/ab/conflict.gz")
    edges = _manifest(
        [_edge("A", "B", update=[update_url])],
        [_edge("A", "B")],
    )
    client = _FakeDatasetsClient(edges)
    payloads = {
        "/p/ab/conflict.gz": _gzip_jsonl(
            [
                _paper(11, "2201.00001", "10.2201/one", 4),
                _paper(11, "2201.00001", "10.2201/one", 5),
            ]
        )
    }
    http = _http(payloads)

    async def publish():
        db.execute("DELETE FROM pending_publish")
        db.execute("UPDATE import_state SET published=true")
        from scholar.etl.arxiv import database_status

        return database_status(db)

    async def invalidate(_):
        return None

    try:
        status = await sync_s2_diffs(
            db,
            client,
            http,
            tmp_path / "diff-work",
            publish_pending=publish,
            invalidate_aliases=invalidate,
            base_release="A",
            target_release="B",
        )
        assert status["complete"] is True
        assert (
            db.execute(
                "SELECT count(*) FROM s2_diff_current_papers WHERE corpus_id='11'"
            ).fetchone()[0]
            == 0
        )
        document = orjson.loads(
            db.execute(
                "SELECT document FROM arxiv WHERE arxiv_id='2201.00001'"
            ).fetchone()[0]
        )
        assert "s2" not in document["metadata"]
        assert (
            db.execute(
                """SELECT count(*) FROM s2_diff_diagnostics
            WHERE reason='corpus_id_conflict'"""
            ).fetchone()[0]
            == 2
        )
    finally:
        await http.aclose()
        db.close()

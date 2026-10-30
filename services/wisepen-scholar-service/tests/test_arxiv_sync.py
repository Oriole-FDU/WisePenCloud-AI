from xml.etree import ElementTree as ET
from unittest.mock import AsyncMock

import orjson
import pytest
from offline.arxiv import open_database
from offline.arxiv_oai import ArxivOaiHarvest, ArxivOaiRecord
from offline import arxiv_sync
from offline.arxiv_sync import recover_s2_missing, sync_arxiv
from offline.embeddings import publication_documents, publication_vectors
from offline.s2_api import S2SyncPaper
from scholar.domain.models.identifiers import IdentifierKind, PaperIdentifier

RAW_NS = "http://arxiv.org/OAI/arXivRaw/"


class FakeHarvester:
    def __init__(self, *harvests):
        self.harvests = list(harvests)
        self.from_dates = []

    async def list_records(self, *, from_date):
        self.from_dates.append(from_date)
        return self.harvests.pop(0)


class FakeS2Client:
    def __init__(self, *outcomes, events=None):
        self.outcomes = list(outcomes)
        self.calls = []
        self.events = events

    async def lookup(self, identifiers):
        if self.events is not None:
            self.events.append("lookup")
        self.calls.append(identifiers)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeCache:
    def __init__(self, *, fail_count=0, events=None):
        self.invalidations = []
        self.fail_count = fail_count
        self.events = events

    async def invalidate(self, identifiers):
        if self.events is not None:
            self.events.append("invalidate")
        self.invalidations.append(list(identifiers))
        if self.fail_count:
            self.fail_count -= 1
            raise RuntimeError("simulated cache invalidation failure")


class FakeArxivCache:
    def __init__(self, *, fail_count=0, events=None):
        self.invalidations = []
        self.fail_count = fail_count
        self.events = events

    async def invalidate(self, identifiers):
        if self.events is not None:
            self.events.append("arxiv_invalidate")
        self.invalidations.append(list(identifiers))
        if self.fail_count:
            self.fail_count -= 1
            raise RuntimeError("simulated arXiv cache invalidation failure")


@pytest.fixture
def db(tmp_path):
    database = open_database(tmp_path / "papers.duckdb")
    yield database
    database.close()


def active_record(
    arxiv_id,
    datestamp,
    *,
    title="A title",
    abstract="An abstract.",
    doi=None,
    authors="A. Author, B. Author",
):
    metadata = ET.Element(f"{{{RAW_NS}}}arXivRaw")
    values = {
        "id": arxiv_id,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "categories": "cs.AI cs.LG",
        "doi": doi,
        "license": "http://arxiv.org/licenses/nonexclusive-distrib/1.0/",
        "journal-ref": "Journal reference",
        "comments": "Comments",
    }
    for key, value in values.items():
        if value is not None:
            ET.SubElement(metadata, f"{{{RAW_NS}}}{key}").text = value
    version = ET.SubElement(metadata, f"{{{RAW_NS}}}version", {"version": "v1"})
    ET.SubElement(version, f"{{{RAW_NS}}}date").text = "Mon, 01 Jan 2024 00:00:00 GMT"
    ET.SubElement(version, f"{{{RAW_NS}}}size").text = "25kb"
    return ArxivOaiRecord(
        identifier=f"oai:arXiv.org:{arxiv_id}",
        datestamp=datestamp,
        deleted=False,
        metadata=metadata,
    )


def deleted_record(arxiv_id, datestamp):
    return ArxivOaiRecord(
        identifier=f"oai:arXiv.org:{arxiv_id}",
        datestamp=datestamp,
        deleted=True,
        metadata=None,
    )


def harvest(response_date, *records):
    return ArxivOaiHarvest(response_date, tuple(records))


def s2_lookup(arxiv_id, doi, corpus_id, *, citation_count=5):
    identifiers = [PaperIdentifier(IdentifierKind.ARXIV, arxiv_id)]
    if doi is not None:
        identifiers.append(PaperIdentifier(IdentifierKind.DOI, doi))
    identifiers.append(PaperIdentifier(IdentifierKind.CORPUS_ID, corpus_id))
    return S2SyncPaper(
        identifiers=identifiers,
        metadata={
            "s2": {
                "corpus_id": corpus_id,
                "paper_id": f"paper-{corpus_id}",
                "citation_count": citation_count,
                "influential_citation_count": 1,
                "is_open_access": False,
                "tldr": {"model": "tldr@v2.0.0", "text": ""},
                "open_access_pdf": None,
                "bibtex": None,
                "observed_at": "2026-10-22T00:00:00+00:00",
            }
        },
        vector=[0.1] * 768,
    )


def seed_paper(db, arxiv_id, doi, *, title="Old title", s2=None):
    document = {
        "arxiv_id": arxiv_id,
        "doi": doi,
        "title": title,
        "abstract": "Old abstract",
        "authors": ["A. Author"],
        "categories": ["cs.AI"],
        "submitted_date": "2024-01-01",
        "updated_date": "2026-10-18",
        "versions": [],
        "journal_ref": None,
        "comments": None,
        "license": None,
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "metadata": {"local": {"kept": True}},
    }
    if s2 is not None:
        document["metadata"]["s2"] = s2
    db.execute(
        "INSERT INTO arxiv VALUES (?, ?, ?::JSON, ?::JSON)",
        [arxiv_id, doi, '{"snapshot":"kaggle"}', orjson.dumps(document).decode()],
    )
    return document


def full_s2(corpus_id, *, citation_count=3):
    return {
        "corpus_id": corpus_id,
        "paper_id": f"paper-{corpus_id}",
        "citation_count": citation_count,
        "influential_citation_count": 0,
        "is_open_access": False,
        "tldr": {"model": "tldr@v2.0.0", "text": "A summary"},
        "open_access_pdf": None,
        "bibtex": None,
        "observed_at": "2026-10-20T00:00:00+00:00",
    }


def publisher(db, *, fail_count=0):
    calls = []

    async def publish():
        nonlocal fail_count
        calls.append(
            {
                "pending_publish": db.execute(
                    "SELECT arxiv_id FROM pending_publish ORDER BY arxiv_id"
                ).fetchall(),
                "pending_delete": db.execute(
                    "SELECT arxiv_id FROM pending_delete ORDER BY arxiv_id"
                ).fetchall(),
            }
        )
        if fail_count:
            fail_count -= 1
            raise RuntimeError("simulated publish failure")
        db.execute("DELETE FROM pending_publish")
        db.execute("DELETE FROM pending_delete")

    return publish, calls


def document_for(db, arxiv_id):
    row = db.execute(
        "SELECT document FROM arxiv WHERE arxiv_id=?", [arxiv_id]
    ).fetchone()
    return None if row is None else orjson.loads(row[0])


def cursor_for(db):
    return db.execute(
        "SELECT last_response_date FROM arxiv_sync_state WHERE singleton=true"
    ).fetchone()


@pytest.mark.asyncio
async def test_oai_update_requests_missing_tldr_even_when_offline_timestamp_is_present(
    db,
):
    arxiv_id = "2401.00001"
    s2 = full_s2("101")
    s2["tldr"] = {"model": "tldr@v2.0.0", "text": None}
    s2["observed_at"] = "2026-10-21T00:00:00Z"
    seed_paper(db, arxiv_id, "10.1234/keep", s2=s2)
    harvester = FakeHarvester(
        harvest(
            "2026-10-22T23:50:00Z",
            active_record(arxiv_id, "2026-10-21", doi="10.1234/keep"),
        )
    )
    enricher = FakeS2Client([s2_lookup(arxiv_id, "10.1234/keep", "101")])
    publish, _ = publisher(db)

    result = await sync_arxiv(
        db,
        harvester,
        enricher,
        publish=publish,
        cache=FakeCache(),
        arxiv_cache=FakeArxivCache(),
    )

    assert result["s2_requested"] == 1
    assert document_for(db, arxiv_id)["metadata"]["s2"]["tldr"]["text"] == ""
    assert result["pending_s2"] == 0


@pytest.mark.asyncio
async def test_two_oai_updates_preserve_s2_and_publish_tombstone_and_new_record(db):
    keep_id = "2401.00001"
    deleted_id = "2401.00002"
    new_id = "2401.00003"
    seed_paper(db, keep_id, "10.1234/keep", s2=full_s2("101"))
    seed_paper(db, deleted_id, "10.1234/delete", s2=full_s2("102"))

    harvester = FakeHarvester(
        harvest(
            "2026-10-22T23:50:00Z",
            active_record(keep_id, "2026-10-21", doi="10.1234/keep"),
        ),
        harvest(
            "2026-10-23T00:02:00Z",
            active_record(
                keep_id,
                "2026-10-22",
                title="Changed again",
                abstract="Changed abstract",
                doi="10.1234/keep",
            ),
            deleted_record(deleted_id, "2026-10-22"),
            active_record(new_id, "2026-10-22", doi="10.1234/new"),
        ),
    )
    enricher = FakeS2Client([None], [None, None])
    cache = FakeCache()
    arxiv_cache = FakeArxivCache()
    publish, publish_calls = publisher(db)

    first = await sync_arxiv(
        db,
        harvester,
        enricher,
        publish=publish,
        cache=cache,
        arxiv_cache=arxiv_cache,
    )
    second = await sync_arxiv(
        db,
        harvester,
        enricher,
        publish=publish,
        cache=cache,
        arxiv_cache=arxiv_cache,
    )

    assert harvester.from_dates == ["2026-10-18", "2026-10-22"]
    assert first["response_date"] == "2026-10-22T23:50:00Z"
    assert second["response_date"] == "2026-10-23T00:02:00Z"
    assert enricher.calls == [
        [PaperIdentifier(IdentifierKind.ARXIV, keep_id)],
        [
            PaperIdentifier(IdentifierKind.ARXIV, keep_id),
            PaperIdentifier(IdentifierKind.ARXIV, new_id),
        ],
    ]
    updated = document_for(db, keep_id)
    assert updated["title"] == "Changed again"
    assert updated["abstract"] == "Changed abstract"
    assert updated["metadata"]["s2"] == full_s2("101")
    assert updated["metadata"]["local"] == {"kept": True}
    assert updated["authors"] == ["A. Author", "B. Author"]
    assert updated["versions"] == [
        {"version": "v1", "created": "Mon, 01 Jan 2024 00:00:00 GMT"}
    ]
    assert updated["submitted_date"] == "2024-01-01"
    assert updated["updated_date"] == "2026-10-22"
    assert document_for(db, deleted_id) is None
    assert document_for(db, new_id)["doi"] == "10.1234/new"
    assert db.execute(
        "SELECT reason FROM pending_s2_core_missing WHERE arxiv_id=?", [new_id]
    ).fetchone() == ("not_found",)
    assert publish_calls[1]["pending_delete"] == [(deleted_id,)]
    assert new_id in [row[0] for row in publish_calls[1]["pending_publish"]]
    assert cursor_for(db) == ("2026-10-23T00:02:00Z",)
    invalidated = {identifier for call in cache.invalidations for identifier in call}
    assert PaperIdentifier(IdentifierKind.DOI, "10.1234/keep") in invalidated
    assert PaperIdentifier(IdentifierKind.CORPUS_ID, "102") in invalidated
    assert {
        PaperIdentifier(IdentifierKind.ARXIV, deleted_id),
        PaperIdentifier(IdentifierKind.DOI, "10.1234/delete"),
        PaperIdentifier(IdentifierKind.CORPUS_ID, "102"),
        PaperIdentifier(IdentifierKind.ARXIV, new_id),
        PaperIdentifier(IdentifierKind.DOI, "10.1234/new"),
    } <= set(arxiv_cache.invalidations[1])


@pytest.mark.asyncio
async def test_doi_identity_change_removes_old_s2_and_revalidates_exact_arxiv_id(db):
    arxiv_id = "2402.00001"
    seed_paper(
        db,
        arxiv_id,
        "10.1234/old",
        s2=full_s2("201"),
    )
    harvester = FakeHarvester(
        harvest(
            "2026-10-24T00:02:00Z",
            active_record(arxiv_id, "2026-10-23", doi="10.1234/new"),
        )
    )
    enricher = FakeS2Client([s2_lookup(arxiv_id, "10.1234/new", "202")])
    cache = FakeCache()
    publish, _ = publisher(db)

    result = await sync_arxiv(
        db,
        harvester,
        enricher,
        publish=publish,
        cache=cache,
        arxiv_cache=FakeArxivCache(),
    )

    assert result["s2_enriched"] == 1
    assert enricher.calls == [[PaperIdentifier(IdentifierKind.ARXIV, arxiv_id)]]
    document = document_for(db, arxiv_id)
    assert document["doi"] == "10.1234/new"
    assert document["metadata"]["s2"]["corpus_id"] == "202"
    assert document["metadata"]["s2"]["citation_count"] == 5
    invalidated = {identifier for call in cache.invalidations for identifier in call}
    assert {
        PaperIdentifier(IdentifierKind.ARXIV, arxiv_id),
        PaperIdentifier(IdentifierKind.DOI, "10.1234/old"),
        PaperIdentifier(IdentifierKind.DOI, "10.1234/new"),
        PaperIdentifier(IdentifierKind.CORPUS_ID, "201"),
        PaperIdentifier(IdentifierKind.CORPUS_ID, "202"),
    } <= invalidated


@pytest.mark.asyncio
async def test_strong_s2_identity_conflicts_are_diagnostic_and_not_projected(db):
    seed_paper(db, "2403.00001", "10.1234/duplicate", s2=full_s2("301"))
    ids = ["2403.00002", "2403.00003", "2403.00004", "2403.00005"]
    records = [
        active_record(ids[0], "2026-10-23", doi="10.1234/duplicate"),
        active_record(ids[1], "2026-10-23", doi="10.1234/expected"),
        active_record(ids[2], "2026-10-23", doi="10.1234/arxiv"),
        active_record(ids[3], "2026-10-23", doi="10.1234/corpus"),
    ]
    results = [
        s2_lookup(ids[0], "10.1234/duplicate", "302"),
        s2_lookup(ids[1], "10.1234/wrong", "303"),
        s2_lookup("2403.99999", "10.1234/arxiv", "304"),
        s2_lookup(ids[3], "10.1234/corpus", "301"),
    ]
    harvester = FakeHarvester(harvest("2026-10-24T00:02:00Z", *records))
    enricher = FakeS2Client(results)
    publish, _ = publisher(db)

    await sync_arxiv(
        db,
        harvester,
        enricher,
        publish=publish,
        cache=FakeCache(),
        arxiv_cache=FakeArxivCache(),
    )

    for arxiv_id in ids:
        document = document_for(db, arxiv_id)
        assert "s2" not in document.get("metadata", {})
    reasons = {
        row[0]
        for row in db.execute(
            "SELECT DISTINCT reason FROM arxiv_sync_diagnostics"
        ).fetchall()
    }
    assert {
        "enrichment_doi_conflict",
        "enrichment_arxiv_conflict",
        "enrichment_corpus_conflict",
    } <= reasons
    assert db.execute("SELECT count(*) FROM pending_s2").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_transient_s2_failure_retries_before_publishing(db, monkeypatch):
    monkeypatch.setattr(arxiv_sync.asyncio, "sleep", AsyncMock())
    arxiv_id = "2404.00001"
    result = s2_lookup(arxiv_id, "10.1234/replay", "401")
    harvester = FakeHarvester(harvest(
        "2026-10-25T00:02:00Z",
        active_record(arxiv_id, "2026-10-24", doi="10.1234/replay"),
    ))
    enricher = FakeS2Client(RuntimeError("simulated S2 failure"), [result])
    publish, publish_calls = publisher(db)
    outcome = await sync_arxiv(
        db, harvester, enricher, publish=publish,
        cache=FakeCache(), arxiv_cache=FakeArxivCache(),
    )
    assert len(enricher.calls) == 2
    assert len(publish_calls) == 1
    assert harvester.from_dates == ["2026-10-18"]
    assert document_for(db, arxiv_id)["metadata"]["s2"]["corpus_id"] == "401"
    assert cursor_for(db) == ("2026-10-25T00:02:00Z",)
    assert outcome["pending_s2"] == 0


@pytest.mark.asyncio
async def test_s2_cache_invalidation_failure_replays_before_lookup(db):
    arxiv_id = "2404.00002"
    same_harvest = harvest(
        "2026-10-25T00:02:00Z",
        active_record(arxiv_id, "2026-10-24", doi="10.1234/cache-replay"),
    )
    harvester = FakeHarvester(same_harvest, same_harvest)
    events = []
    enricher = FakeS2Client([None], events=events)
    cache = FakeCache(fail_count=1, events=events)
    publish, _ = publisher(db)

    with pytest.raises(RuntimeError, match="simulated cache invalidation failure"):
        await sync_arxiv(
            db,
            harvester,
            enricher,
            publish=publish,
            cache=cache,
            arxiv_cache=FakeArxivCache(),
        )
    assert events == ["invalidate"]
    assert cursor_for(db) is None
    assert enricher.calls == []

    await sync_arxiv(
        db,
        harvester,
        enricher,
        publish=publish,
        cache=cache,
        arxiv_cache=FakeArxivCache(),
    )

    assert events == ["invalidate", "invalidate", "lookup"]
    assert harvester.from_dates == ["2026-10-18", "2026-10-18"]
    assert cursor_for(db) == ("2026-10-25T00:02:00Z",)
    assert PaperIdentifier(IdentifierKind.ARXIV, arxiv_id) in cache.invalidations[1]


@pytest.mark.asyncio
async def test_publish_failure_does_not_advance_cursor_and_replays_base_record(db):
    arxiv_id = "2405.00001"
    same_harvest = harvest(
        "2026-10-26T00:02:00Z",
        active_record(arxiv_id, "2026-10-25", doi="10.1234/publish"),
    )
    harvester = FakeHarvester(same_harvest, same_harvest)
    enricher = FakeS2Client([None], [None])
    publish, publish_calls = publisher(db, fail_count=1)
    cache = FakeCache()

    with pytest.raises(RuntimeError, match="simulated publish failure"):
        await sync_arxiv(
            db,
            harvester,
            enricher,
            publish=publish,
            cache=cache,
            arxiv_cache=FakeArxivCache(),
        )
    assert cursor_for(db) is None
    assert db.execute("SELECT count(*) FROM pending_publish").fetchone()[0] == 1

    await sync_arxiv(
        db,
        harvester,
        enricher,
        publish=publish,
        cache=cache,
        arxiv_cache=FakeArxivCache(),
    )

    assert harvester.from_dates == ["2026-10-18", "2026-10-18"]
    assert cursor_for(db) == ("2026-10-26T00:02:00Z",)
    assert len(publish_calls) == 2
    assert publish_calls[0]["pending_publish"] == [(arxiv_id,)]


@pytest.mark.asyncio
async def test_arxiv_cache_failure_after_publish_replays_aliases_before_checkpoint(db):
    arxiv_id = "2405.00002"
    seed_paper(db, arxiv_id, "10.1234/old-cache", s2=full_s2("501"))
    same_harvest = harvest(
        "2026-10-26T00:02:00Z",
        active_record(
            arxiv_id, "2026-10-25", title="Updated title", doi="10.1234/new-cache"
        ),
    )
    harvester = FakeHarvester(same_harvest, same_harvest)
    events = []
    client = FakeS2Client([None], [None], events=events)
    s2_cache = FakeCache(events=events)
    arxiv_cache = FakeArxivCache(fail_count=1, events=events)
    publish, publish_calls = publisher(db)

    async def publish_with_event():
        events.append("publish")
        await publish()

    with pytest.raises(
        RuntimeError, match="simulated arXiv cache invalidation failure"
    ):
        await sync_arxiv(
            db,
            harvester,
            client,
            publish=publish_with_event,
            cache=s2_cache,
            arxiv_cache=arxiv_cache,
        )

    assert events == ["invalidate", "lookup", "publish", "arxiv_invalidate"]
    assert cursor_for(db) is None
    assert db.execute("SELECT count(*) FROM pending_publish").fetchone()[0] == 0
    assert len(publish_calls) == 1

    await sync_arxiv(
        db,
        harvester,
        client,
        publish=publish_with_event,
        cache=s2_cache,
        arxiv_cache=arxiv_cache,
    )

    expected_aliases = {
        PaperIdentifier(IdentifierKind.ARXIV, arxiv_id),
        PaperIdentifier(IdentifierKind.DOI, "10.1234/old-cache"),
        PaperIdentifier(IdentifierKind.DOI, "10.1234/new-cache"),
        PaperIdentifier(IdentifierKind.CORPUS_ID, "501"),
    }
    assert expected_aliases <= set(arxiv_cache.invalidations[0])
    assert arxiv_cache.invalidations[0] == arxiv_cache.invalidations[1]
    assert events == [
        "invalidate",
        "lookup",
        "publish",
        "arxiv_invalidate",
        "invalidate",
        "lookup",
        "arxiv_invalidate",
    ]
    assert len(publish_calls) == 1
    assert harvester.from_dates == ["2026-10-18", "2026-10-18"]
    assert cursor_for(db) == ("2026-10-26T00:02:00Z",)


@pytest.mark.asyncio
async def test_recovery_without_oai_records_invalidates_arxiv_cache_after_publish(db):
    arxiv_id = "2501.12949"
    harvester = FakeHarvester(
        harvest("2026-10-28T00:00:00Z", active_record(arxiv_id, "2026-10-28")),
        harvest("2026-10-29T00:00:00Z"),
    )
    events = []
    client = FakeS2Client([None], [s2_lookup(arxiv_id, None, "701")], events=events)
    s2_cache = FakeCache(events=events)
    arxiv_cache = FakeArxivCache(events=events)
    publish, publish_calls = publisher(db)

    async def publish_with_event():
        events.append("publish")
        await publish()

    first = await sync_arxiv(
        db, harvester, client, publish=publish_with_event,
        cache=s2_cache, arxiv_cache=arxiv_cache,
    )
    assert first["pending_s2"] == 0
    assert db.execute("SELECT reason FROM pending_s2_core_missing").fetchall() == [("not_found",)]
    recovered = await recover_s2_missing(db, client, publish=publish_with_event, cache=s2_cache)
    second = await sync_arxiv(
        db, harvester, client, publish=publish_with_event,
        cache=s2_cache, arxiv_cache=arxiv_cache,
    )
    assert recovered["recovered"] == 1
    assert recovered["remaining"] == 0
    assert second["harvested"] == 0
    assert second["pending_s2"] == 0
    assert PaperIdentifier(IdentifierKind.CORPUS_ID, "701") in arxiv_cache.invalidations[-1]
    assert max(i for i, event in enumerate(events) if event == "publish") < max(
        i for i, event in enumerate(events) if event == "arxiv_invalidate")
    assert cursor_for(db) == ("2026-10-29T00:00:00Z",)
    assert len(publish_calls) == 2
    rows = db.execute("SELECT arxiv_id,document FROM arxiv WHERE arxiv_id=?", [arxiv_id]).fetchall()
    assert publication_documents(rows)[0]["metadata"]["s2"]["corpus_id"] == "701"
    assert publication_vectors(db, rows)[arxiv_id] == pytest.approx([0.1] * 768)


@pytest.mark.asyncio
async def test_exhausted_metadata_refresh_keeps_pending_for_next_sync(db, monkeypatch):
    monkeypatch.setattr(arxiv_sync.asyncio, "sleep", AsyncMock())
    arxiv_id = "2501.12948"
    harvester = FakeHarvester(
        harvest("2026-10-26T00:00:00Z", active_record(arxiv_id, "2026-10-26")),
        harvest("2026-10-27T00:00:00Z", active_record(arxiv_id, "2026-10-27")),
        harvest("2026-10-27T00:00:00Z", active_record(arxiv_id, "2026-10-27")),
    )
    refreshed = s2_lookup(arxiv_id, None, "201", citation_count=7)
    enricher = FakeS2Client(
        [s2_lookup(arxiv_id, None, "201")],
        *(RuntimeError("429") for _ in range(5)), [refreshed],
    )
    publish, _ = publisher(db)
    await sync_arxiv(db, harvester, enricher, publish=publish,
                     cache=FakeCache(), arxiv_cache=FakeArxivCache())
    failed = await sync_arxiv(db, harvester, enricher, publish=publish,
                              cache=FakeCache(), arxiv_cache=FakeArxivCache())
    # 当前离线基线：S2 重试耗尽不阻塞 OAI 游标，但保留待办及旧的有效向量。
    assert failed["pending_s2"] == 1
    assert cursor_for(db) == ("2026-10-27T00:00:00Z",)
    assert db.execute("SELECT count(*) FROM pending_embeddings").fetchone()[0] == 0
    result = await sync_arxiv(db, harvester, enricher, publish=publish,
                              cache=FakeCache(), arxiv_cache=FakeArxivCache())
    assert len(enricher.calls) == 7
    assert result["pending_s2"] == 0
    assert document_for(db, arxiv_id)["metadata"]["s2"]["citation_count"] == 7

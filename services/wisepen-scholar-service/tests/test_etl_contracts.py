"""P1 ETL 的可观察 checkpoint 与版本发布行为。"""

import orjson
import pytest

from scholar.etl.arxiv import database_status, import_arxiv, open_database
from scholar.etl.publish import publish_arxiv


@pytest.fixture
def snapshot_rows():
    return [
        {
            "id": "2201.00001",
            "doi": "10.5555/p1.first",
            "title": "First   paper",
            "abstract": "A short abstract.",
            "authors_parsed": [["Doe", "Jane", "van"]],
            "categories": "cs.AI",
            "versions": [{"version": "v1", "created": "Mon, 03 Jan 2022 00:00:00 GMT"}],
            "update_date": "2022-01-05",
            "journal-ref": None,
            "comments": None,
            "license": None,
        },
        {
            "id": "2202.00002",
            "doi": None,
            "title": "Second paper",
            "abstract": "Another short abstract.",
            "authors_parsed": [["Roe", "John", ""]],
            "categories": "cs.CL",
            "versions": [{"version": "v1", "created": "Tue, 01 Feb 2022 00:00:00 GMT"}],
            "update_date": "2022-02-02",
            "journal-ref": None,
            "comments": None,
            "license": None,
        },
    ]


@pytest.fixture
def snapshot_file(tmp_path, snapshot_rows):
    path = tmp_path / "arxiv.jsonl"
    path.write_bytes(b"\n".join(orjson.dumps(row) for row in snapshot_rows) + b"\n")
    return path


@pytest.fixture
def completed_database(tmp_path, snapshot_file):
    with open_database(tmp_path / "papers.duckdb") as db:
        import_arxiv(db, snapshot_file)
        yield db


def test_import_resumes_from_checkpoint_and_repeated_run_is_idempotent(
    tmp_path, snapshot_file
):
    with open_database(tmp_path / "papers.duckdb") as db:
        first = import_arxiv(db, snapshot_file, batch_size=1, max_batches=1)
        assert first["papers"] == 1
        assert first["pending"] == 1
        assert first["import"]["complete"] is False

        resumed = import_arxiv(db, snapshot_file, batch_size=1)
        assert resumed["papers"] == 2
        assert resumed["pending"] == 2
        assert resumed["import"]["records"] == 2
        assert resumed["import"]["complete"] is True

        repeated = import_arxiv(db, snapshot_file, batch_size=1)
        assert repeated == resumed


def test_import_rejects_a_changed_input_after_checkpoint(
    tmp_path, snapshot_file, snapshot_rows
):
    with open_database(tmp_path / "papers.duckdb") as db:
        import_arxiv(db, snapshot_file, batch_size=1, max_batches=1)
        with snapshot_file.open("ab") as source:
            source.write(orjson.dumps(snapshot_rows[1]) + b"\n")

        with pytest.raises(ValueError):
            import_arxiv(db, snapshot_file, batch_size=1)


class FakeIndices:
    """只模拟 publish_arxiv 使用的 ES 边界，不替代真实 ES 验收。"""

    def __init__(self, es):
        self.es = es

    async def exists(self, *, index):
        return index in self.es.documents

    async def create(self, *, index, mappings, settings):
        self.es.documents[index] = {}

    async def refresh(self, *, index):
        self.es.refreshes.append(index)

    async def put_settings(self, *, index, settings):
        self.es.settings_updates.append(index)

    async def update_aliases(self, *, actions):
        self.es.alias_updates.append(actions)


class FakeElasticsearch:
    """记录发布协议副作用，可注入逐项失败、count 错误或抽样不一致。"""

    def __init__(self, *, bulk_statuses=(), count_override=None, corrupt_sample=False):
        self.documents = {}
        self.bulk_statuses = list(bulk_statuses)
        self.count_override = count_override
        self.corrupt_sample = corrupt_sample
        self.bulk_calls = []
        self.count_calls = []
        self.mget_calls = []
        self.refreshes = []
        self.settings_updates = []
        self.alias_updates = []
        self.indices = FakeIndices(self)

    async def bulk(self, *, operations):
        self.bulk_calls.append(operations)
        target = operations[0]["index"]["_index"]
        statuses = (
            self.bulk_statuses.pop(0)
            if self.bulk_statuses
            else [201] * (len(operations) // 2)
        )
        items = []
        for position, status in enumerate(statuses):
            action = operations[position * 2]["index"]
            document = operations[position * 2 + 1]
            if 200 <= status < 300:
                self.documents[target][action["_id"]] = document
                items.append({"index": {"status": status}})
            else:
                items.append(
                    {
                        "index": {
                            "status": status,
                            "error": {"type": "temporary_failure"},
                        }
                    }
                )
        return {"items": items}

    async def count(self, *, index):
        self.count_calls.append(index)
        count = len(self.documents[index])
        return {"count": count if self.count_override is None else self.count_override}

    async def mget(self, *, index, ids):
        self.mget_calls.append((index, ids))
        docs = []
        for arxiv_id in ids:
            source = self.documents[index].get(arxiv_id)
            if source is not None and self.corrupt_sample:
                source = {**source, "title": "different projection"}
            docs.append(
                {"_id": arxiv_id, "found": source is not None, "_source": source}
            )
        return {"docs": docs}


@pytest.mark.asyncio
async def test_publish_acknowledges_only_successes_replays_failures_and_is_idempotent(
    completed_database,
):
    # 成功与失败处于同一 bulk 时，只确认成功项；下一次调用应只重放剩余论文。
    es = FakeElasticsearch(bulk_statuses=[[201, 429]])
    with pytest.raises(RuntimeError):
        await publish_arxiv(completed_database, es, "papers")

    failed_status = database_status(completed_database)
    assert failed_status["papers"] == 2
    assert failed_status["pending"] == 1
    target = next(iter(es.documents))
    assert set(es.documents[target]) == {"2201.00001"}
    assert es.count_calls == []
    assert es.mget_calls == []
    assert es.alias_updates == []

    published = await publish_arxiv(completed_database, es, "papers")
    assert published["pending"] == 0
    assert published["import"]["published"] is True
    assert [
        [call[index]["index"]["_id"] for index in range(0, len(call), 2)]
        for call in es.bulk_calls
    ] == [["2201.00001", "2202.00002"], ["2202.00002"]]
    assert len(es.count_calls) == 1
    assert es.mget_calls == [(target, ["2201.00001", "2202.00002"])]
    assert len(es.alias_updates) == 1

    # 已发布数据库的重复运行不会重复写入或再次切换别名。
    await publish_arxiv(completed_database, es, "papers")
    assert len(es.bulk_calls) == 2
    assert len(es.alias_updates) == 1
    assert len(es.documents[target]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("count_override", "corrupt_sample"),
    [(0, False), (None, True)],
)
async def test_publish_never_switches_alias_before_count_and_sample_match(
    completed_database, count_override, corrupt_sample
):
    es = FakeElasticsearch(count_override=count_override, corrupt_sample=corrupt_sample)

    with pytest.raises(RuntimeError):
        await publish_arxiv(completed_database, es, "papers")

    assert database_status(completed_database)["pending"] == 0
    assert es.alias_updates == []


@pytest.mark.asyncio
async def test_duplicate_snapshot_ids_keep_latest_source_metadata(
    tmp_path, snapshot_rows
):
    # 真实快照含重复 ID；更新日期较旧的后续行不能覆盖较新的来源事实。
    newer = {**snapshot_rows[0], "title": "Latest title", "update_date": "2026-06-03"}
    older = {**snapshot_rows[0], "title": "Previous title", "update_date": "2020-01-01"}
    source = tmp_path / "duplicates.jsonl"
    source.write_bytes(orjson.dumps(newer) + b"\n" + orjson.dumps(older) + b"\n")
    with open_database(tmp_path / "papers.duckdb") as db:
        imported = import_arxiv(db, source, batch_size=1)
        assert imported["papers"] == 1
        es = FakeElasticsearch()
        result = await publish_arxiv(db, es, "papers")
        assert (
            es.documents[result["import"]["target_index"]]["2201.00001"]["title"]
            == "Latest title"
        )

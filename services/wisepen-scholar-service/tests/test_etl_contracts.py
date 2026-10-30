"""ETL checkpoint、全量发布与增量发布的可观察行为。"""

from types import SimpleNamespace

import orjson
import pytest
from qdrant_client.http.models import Distance, VectorParams
from offline.arxiv import database_status, import_arxiv, open_database
from offline.publish import publish_arxiv
from scholar.core.storage.qdrant.paper_vectors import point_id
from scholar.ml.specter import DIMENSIONS


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
        self.created_mappings = []
        self.updated_properties = []

    async def exists(self, *, index):
        return index in self.es.documents

    async def create(self, *, index, mappings, settings):
        self.es.documents[index] = {}
        self.created_mappings.append(mappings)

    async def put_mapping(self, *, index, properties):
        self.updated_properties.append(properties)

    async def refresh(self, *, index):
        self.es.refreshes.append(index)

    async def put_settings(self, *, index, settings):
        self.es.settings_updates.append(index)

    async def update_aliases(self, *, actions):
        if self.es.fail_alias_update:
            raise RuntimeError("simulated alias update failure")
        self.es.alias_updates.append(actions)


class FakeElasticsearch:
    """记录发布协议副作用，可注入逐项、计数、抽样或别名失败。"""

    def __init__(
        self,
        *,
        bulk_statuses=(),
        count_override=None,
        corrupt_sample=False,
        fail_alias_update=False,
    ):
        self.documents = {}
        self.bulk_statuses = list(bulk_statuses)
        self.count_override = count_override
        self.corrupt_sample = corrupt_sample
        self.fail_alias_update = fail_alias_update
        self.bulk_calls = []
        self.count_calls = []
        self.mget_calls = []
        self.refreshes = []
        self.settings_updates = []
        self.alias_updates = []
        self.indices = FakeIndices(self)

    async def bulk(self, *, operations):
        self.bulk_calls.append(operations)
        work = []
        target = None
        offset = 0
        while offset < len(operations):
            operation = operations[offset]
            action = next(iter(operation))
            details = operation[action]
            target = details["_index"]
            if action == "delete":
                work.append((action, details["_id"], None))
                offset += 1
            else:
                work.append((action, details["_id"], operations[offset + 1]))
                offset += 2
        statuses = (
            self.bulk_statuses.pop(0)
            if self.bulk_statuses
            else [
                201
                if action == "index"
                else 200
                if arxiv_id in self.documents[target]
                else 404
                for action, arxiv_id, _ in work
            ]
        )
        items = []
        for (action, arxiv_id, document), status in zip(work, statuses, strict=True):
            if action == "index" and 200 <= status < 300:
                self.documents[target][arxiv_id] = document
                items.append({action: {"status": status}})
            elif action == "delete" and 200 <= status < 300:
                self.documents[target].pop(arxiv_id, None)
                items.append({action: {"status": status}})
            elif action == "delete" and status == 404:
                items.append({action: {"status": status}})
            else:
                items.append(
                    {
                        action: {
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


class FakeQdrant:
    """模拟离线发布依赖的 Qdrant collection 与 point 写入边界。"""

    def __init__(self, *, fail_upsert=False, fail_delete=False):
        self.exists = False
        self.points = {}
        self.fail_upsert = fail_upsert
        self.fail_delete = fail_delete
        self.upsert_calls = []
        self.delete_calls = []

    async def collection_exists(self, *, collection_name):
        return self.exists

    async def create_collection(self, *, collection_name, vectors_config, hnsw_config):
        self.exists = True

    async def get_collection(self, *, collection_name):
        vectors = VectorParams(size=DIMENSIONS, distance=Distance.EUCLID)
        return SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(vectors=vectors))
        )

    async def create_payload_index(self, *, collection_name, field_name, field_schema):
        pass

    async def upsert(self, *, collection_name, points, wait):
        self.upsert_calls.append(points)
        if self.fail_upsert:
            raise RuntimeError("simulated Qdrant upsert failure")
        self.points.update((point.id, point) for point in points)

    async def delete(self, *, collection_name, points_selector, wait):
        self.delete_calls.append(points_selector.points)
        if self.fail_delete:
            raise RuntimeError("simulated Qdrant delete failure")
        for identifier in points_selector.points:
            self.points.pop(identifier, None)


@pytest.fixture
def qdrant():
    return FakeQdrant()


@pytest.mark.asyncio
async def test_publish_acknowledges_only_successes_replays_failures_and_is_idempotent(
    completed_database, qdrant
):
    # 成功与失败处于同一 bulk 时，只确认成功项；下一次调用应只重放剩余论文。
    es = FakeElasticsearch(bulk_statuses=[[201, 429]])
    with pytest.raises(RuntimeError):
        await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)

    failed_status = database_status(completed_database)
    assert failed_status["papers"] == 2
    assert failed_status["pending"] == 1
    target = next(iter(es.documents))
    assert set(es.documents[target]) == {"2201.00001"}
    assert es.count_calls == []
    assert es.mget_calls == []
    assert es.alias_updates == []

    published = await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)
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
    await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)
    assert len(es.bulk_calls) == 2
    assert len(es.alias_updates) == 1
    assert len(es.documents[target]) == 2


@pytest.mark.asyncio
async def test_initial_publish_deletes_tombstones_before_alias_switch(
    completed_database, qdrant
):
    completed_database.execute(
        "INSERT INTO pending_delete VALUES ('2101.00001', '2026-10-27')"
    )
    es = FakeElasticsearch(fail_alias_update=True)

    with pytest.raises(RuntimeError, match="alias update failure"):
        await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)

    assert (
        completed_database.execute("SELECT arxiv_id FROM pending_delete").fetchall()
        == []
    )
    assert es.bulk_calls[-1] == [
        {"delete": {"_index": next(iter(es.documents)), "_id": "2101.00001"}}
    ]
    assert es.alias_updates == []
    assert database_status(completed_database)["import"]["published"] is False

    es.fail_alias_update = False
    result = await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)

    assert result["pending_delete"] == 0
    assert es.alias_updates
    assert database_status(completed_database)["import"]["published"] is True
    assert len(es.bulk_calls) == 2


@pytest.mark.asyncio
async def test_unpublished_existing_target_replays_failed_tombstone_before_validation(
    completed_database, qdrant
):
    target = "papers-existing"
    old_documents = {
        arxiv_id: orjson.loads(document)
        for arxiv_id, document in completed_database.execute(
            "SELECT arxiv_id,document FROM arxiv"
        ).fetchall()
    }
    completed_database.execute(
        "UPDATE import_state SET target_index=?,published=false", [target]
    )
    completed_database.execute("DELETE FROM arxiv WHERE arxiv_id='2202.00002'")
    completed_database.execute(
        "DELETE FROM pending_publish WHERE arxiv_id='2202.00002'"
    )
    completed_database.execute(
        "INSERT INTO pending_delete VALUES ('2202.00002', '2026-10-27')"
    )
    es = FakeElasticsearch(bulk_statuses=[[201], [429]])
    es.documents[target] = old_documents

    with pytest.raises(RuntimeError, match="full ES delete failed"):
        await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)

    assert completed_database.execute(
        "SELECT arxiv_id FROM pending_delete"
    ).fetchall() == [("2202.00002",)]
    assert (
        completed_database.execute("SELECT arxiv_id FROM pending_publish").fetchall()
        == []
    )
    assert "2202.00002" in es.documents[target]
    assert "embedding" not in es.indices.updated_properties[0]
    assert es.count_calls == []
    assert es.alias_updates == []

    result = await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)

    assert result["pending_delete"] == 0
    assert result["import"]["published"] is True
    assert "2202.00002" not in es.documents[target]
    assert es.count_calls == [target]
    assert len(es.alias_updates) == 1
    assert es.bulk_calls[-1] == [{"delete": {"_index": target, "_id": "2202.00002"}}]


@pytest.mark.asyncio
async def test_incremental_publish_indexes_and_deletes_with_pending_replay(
    completed_database, qdrant
):
    es = FakeElasticsearch()
    initial = await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)
    target = initial["import"]["target_index"]
    updated = completed_database.execute(
        "SELECT document FROM arxiv WHERE arxiv_id='2201.00001'"
    ).fetchone()[0]
    document = orjson.loads(updated)
    document["title"] = "Updated title"
    completed_database.execute(
        "UPDATE arxiv SET document=?::JSON WHERE arxiv_id='2201.00001'",
        [orjson.dumps(document).decode()],
    )
    completed_database.execute("INSERT INTO pending_publish VALUES ('2201.00001')")
    completed_database.execute("DELETE FROM arxiv WHERE arxiv_id='2202.00002'")
    completed_database.execute(
        "INSERT INTO pending_delete VALUES ('2202.00002', '2026-10-27')"
    )
    es.bulk_statuses.append([201, 429])

    with pytest.raises(RuntimeError, match="incremental ES bulk failed"):
        await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)

    assert (
        completed_database.execute("SELECT arxiv_id FROM pending_publish").fetchall()
        == [("2201.00001",)]
    )
    assert completed_database.execute(
        "SELECT arxiv_id FROM pending_delete"
    ).fetchall() == [("2202.00002",)]
    assert es.documents[target]["2201.00001"]["title"] == "Updated title"
    assert "2202.00002" in es.documents[target]
    assert len(es.alias_updates) == 1

    # 模拟远端已删除、但本地尚未确认的崩溃点；重放的 404 应确认删除。
    del es.documents[target]["2202.00002"]
    replayed = await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)

    assert replayed["pending"] == 0
    assert replayed["pending_delete"] == 0
    assert replayed["import"]["published"] is True
    assert "2202.00002" not in es.documents[target]
    assert len(es.alias_updates) == 1
    # 增量基线在 ES、Qdrant 整批完成后确认；此前 ES 成功项也需要幂等重放。
    actions = [
        (action, details["_id"])
        for operation in es.bulk_calls[-1]
        for action, details in operation.items()
        if action in {"index", "delete"}
    ]
    assert actions == [("index", "2201.00001"), ("delete", "2202.00002")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("count_override", "corrupt_sample"),
    [(0, False), (None, True)],
)
async def test_publish_never_switches_alias_before_count_and_sample_match(
    completed_database, qdrant, count_override, corrupt_sample
):
    es = FakeElasticsearch(count_override=count_override, corrupt_sample=corrupt_sample)

    with pytest.raises(RuntimeError):
        await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)

    assert database_status(completed_database)["pending"] == 0
    assert es.alias_updates == []


@pytest.mark.asyncio
async def test_duplicate_snapshot_ids_keep_latest_source_metadata(
    tmp_path, snapshot_rows, qdrant
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
        result = await publish_arxiv(db, es, "papers", qdrant=qdrant)
        assert (
            es.documents[result["import"]["target_index"]]["2201.00001"]["title"]
            == "Latest title"
        )


@pytest.mark.asyncio
async def test_embeddings_reuse_and_publish_separately_from_es_documents(
    completed_database, qdrant
):
    from offline.embeddings import generate_embeddings

    class Worker:
        def __init__(self):
            self.inputs = []

        async def encode(self, documents):
            self.inputs.extend(documents)
            return [[1.0] * 768 for _ in documents]

    worker, es = Worker(), FakeElasticsearch()
    await generate_embeddings(completed_database, worker)
    await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)
    index = completed_database.execute(
        "SELECT target_index FROM import_state"
    ).fetchone()[0]
    assert all("embedding" not in doc for doc in es.documents[index].values())
    assert "embedding" not in es.indices.created_mappings[0]["properties"]
    assert "_meta" not in es.indices.created_mappings[0]
    point = qdrant.points[point_id("2201.00001")]
    assert point.vector == [1.0] * DIMENSIONS
    assert point.payload == {
        "arxiv_id": "2201.00001",
        "categories": ["cs.AI"],
        "category_groups": ["cs"],
        "submitted_date": "2022-01-03T00:00:00Z",
    }
    assert len(worker.inputs) == 2
    assert (await generate_embeddings(completed_database, worker))["written"] == 0
    # S2 只变更元数据时沿用同一向量，全文投影与向量投影都可幂等重放。
    completed_database.execute(
        'UPDATE arxiv SET document=json_merge_patch(document, \'{"metadata":{"s2":{"citation_count":0}}}\')'
    )
    completed_database.execute(
        "INSERT OR IGNORE INTO pending_publish SELECT arxiv_id FROM arxiv"
    )
    await generate_embeddings(completed_database, worker)
    await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)
    assert len(worker.inputs) == 2
    assert all(
        doc["metadata"]["s2"]["citation_count"] == 0 and "embedding" not in doc
        for doc in es.documents[index].values()
    )
    # 标题变化使输入指纹失效；先删除旧 Qdrant point，再由 GPU 编码器补入新向量。
    completed_database.execute(
        "UPDATE arxiv SET document=json_merge_patch(document, '{\"title\":\"Changed title\"}') WHERE arxiv_id='2201.00001'"
    )
    completed_database.execute("INSERT INTO pending_publish VALUES ('2201.00001')")
    await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)
    assert "embedding" not in es.documents[index]["2201.00001"]
    assert point_id("2201.00001") not in qdrant.points
    await generate_embeddings(completed_database, worker)
    await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)
    assert len(worker.inputs) == 3
    assert "embedding" not in es.documents[index]["2201.00001"]
    assert qdrant.points[point_id("2201.00001")].vector == [1.0] * DIMENSIONS

    completed_database.execute("DELETE FROM arxiv WHERE arxiv_id='2202.00002'")
    completed_database.execute(
        "INSERT INTO pending_delete VALUES ('2202.00002', '2026-10-27')"
    )
    await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)
    assert "2202.00002" not in es.documents[index]
    assert point_id("2202.00002") not in qdrant.points


@pytest.mark.asyncio
async def test_qdrant_write_failure_keeps_pending_for_idempotent_replay(
    completed_database,
):
    from offline.embeddings import generate_embeddings

    class Worker:
        async def encode(self, documents):
            return [[2.0] * DIMENSIONS for _ in documents]

    es = FakeElasticsearch()
    qdrant = FakeQdrant(fail_upsert=True)
    await generate_embeddings(completed_database, Worker())

    with pytest.raises(RuntimeError, match="simulated Qdrant upsert failure"):
        await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)

    assert database_status(completed_database)["pending"] == 2
    target = next(iter(es.documents))
    assert set(es.documents[target]) == {"2201.00001", "2202.00002"}
    assert qdrant.points == {}

    qdrant.fail_upsert = False
    await publish_arxiv(completed_database, es, "papers", qdrant=qdrant)
    assert database_status(completed_database)["pending"] == 0
    assert set(qdrant.points) == {
        point_id("2201.00001"),
        point_id("2202.00002"),
    }


@pytest.mark.asyncio
async def test_failed_gpu_batch_leaves_work_replayable(completed_database):
    from offline.embeddings import generate_embeddings

    class Worker:
        async def encode(self, documents):
            raise RuntimeError("GPU failure")

    with pytest.raises(RuntimeError, match="GPU failure"):
        await generate_embeddings(completed_database, Worker())
    assert (
        completed_database.execute("SELECT count(*) FROM paper_embeddings").fetchone()[
            0
        ]
        == 0
    )
    assert (
        completed_database.execute(
            "SELECT count(*) FROM pending_embeddings"
        ).fetchone()[0]
        == 2
    )

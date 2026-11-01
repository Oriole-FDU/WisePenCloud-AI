"""回填只更新标题派生字段，失败批次不推进断点，恢复后按同一批重放。"""

import json
import asyncio
from unittest.mock import AsyncMock
from types import SimpleNamespace

import httpx
import pytest
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from backfill_titles import backfill, verify


@pytest.mark.asyncio
async def test_payload_backfill_resumes_after_unconfirmed_batch_without_changing_vectors(tmp_path):
    client = AsyncQdrantClient(":memory:")
    journal = tmp_path / "title.json"
    try:
        await client.create_collection("titles", vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE))
        await client.upsert("titles", points=[
            models.PointStruct(id=i, vector=[1.0, 0.0], payload={"title": f"  TITLE {i} ", "keep": i}) for i in range(1, 4)
        ])
        original = client.batch_update_points
        calls = 0

        async def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("unconfirmed batch")
            return await original(*args, **kwargs)

        client.batch_update_points = fail_second
        with pytest.raises(RuntimeError, match="unconfirmed"):
            await backfill(client, "titles", journal, batch_size=1)
        assert json.loads(journal.read_text())["accepted"] == 1
        client.batch_update_points = original
        result = await backfill(client, "titles", journal, batch_size=1, resume=True)
        assert result["stage"] == "submitted" and result["accepted"] == 3
        points, _ = await client.scroll("titles", with_vectors=True)
        assert [p.payload for p in points] == [{"title": f"  TITLE {i} ", "keep": i, "normalized_title": f"title {i}"} for i in range(1, 4)]
        assert all(point.vector == [1.0, 0.0] for point in points)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_transport_failure_after_write_replays_without_confirming_a_gap(tmp_path):
    client = AsyncQdrantClient(":memory:")
    journal = tmp_path / "retry.json"
    try:
        await client.create_collection("titles", vectors_config={})
        await client.upsert("titles", points=[
            models.PointStruct(id=i, vector={}, payload={"title": f"Title {i}"}) for i in (1, 2)
        ])
        original = client.batch_update_points
        later_applied = asyncio.Event()
        attempts = {1: 0, 2: 0}

        async def uncertain_write(*args, **kwargs):
            identifier = kwargs["update_operations"][0].set_payload.points[0]
            attempts[identifier] += 1
            result = await original(*args, **kwargs)
            if identifier == 1 and attempts[identifier] == 1:
                # 服务器已应用，但客户端丢失响应；只有重放收到确认后才能推进断点。
                raise ResponseHandlingException(httpx.ReadTimeout(""))
            if identifier == 2:
                later_applied.set()
            return result

        client.batch_update_points = uncertain_write
        task = asyncio.create_task(backfill(client, "titles", journal, batch_size=1, concurrency=2))
        try:
            await asyncio.wait_for(later_applied.wait(), 2)
            assert not journal.exists()
            result = await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert result["stage"] == "submitted" and result["accepted"] == 2
        assert attempts == {1: 2, 2: 1}
        points, _ = await client.scroll("titles")
        assert [p.payload["normalized_title"] for p in points] == ["title 1", "title 2"]
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("transient", [True, False])
async def test_backfill_reports_underlying_error_and_does_not_retry_bad_requests(tmp_path, monkeypatch, transient):
    client = AsyncQdrantClient(":memory:")
    journal = tmp_path / "failed.json"
    error = ResponseHandlingException(httpx.ReadTimeout("")) if transient else UnexpectedResponse(400, "Bad Request", b"bad filter", httpx.Headers())
    scroll = AsyncMock(side_effect=error)
    client.scroll = scroll
    monkeypatch.setattr("backfill_titles.asyncio.sleep", AsyncMock())
    try:
        with pytest.raises(type(error)) as caught:
            await backfill(client, "titles", journal)
        assert scroll.await_count == (4 if transient else 1)
        assert any(("ReadTimeout" if transient else "UnexpectedResponse") in note for note in caught.value.__notes__)
        assert not journal.exists()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_bounded_run_can_resume_existing_journal_with_another_batch_size(tmp_path):
    client = AsyncQdrantClient(":memory:")
    journal = tmp_path / "bounded.json"
    try:
        await client.create_collection("titles", vectors_config={})
        await client.upsert("titles", points=[models.PointStruct(id=i, vector={}, payload={"title": f"Title {i}"}) for i in range(1, 6)])
        partial = await backfill(client, "titles", journal, batch_size=1, concurrency=2, max_batches=2)
        assert partial["accepted"] == 2 and partial["stage"] == "upload"
        result = await backfill(client, "titles", journal, batch_size=3, concurrency=1, resume=True)
        assert result["accepted"] == 5 and result["stage"] == "submitted"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ack_records_submission_without_readback_indexing_or_verification(tmp_path):
    client = AsyncQdrantClient(":memory:")
    journal = tmp_path / "acknowledged.json"
    try:
        await client.create_collection("titles", vectors_config={})
        await client.upsert("titles", points=[models.PointStruct(id=1, vector={}, payload={"title": "Title"})])
        client.batch_update_points = AsyncMock(return_value=[models.UpdateResult(operation_id=1, status=models.UpdateStatus.ACKNOWLEDGED)])
        client.retrieve = AsyncMock(side_effect=AssertionError("submission must not read back"))
        client.count = AsyncMock(side_effect=AssertionError("submission must not count"))
        client.create_payload_index = AsyncMock(side_effect=AssertionError("submission must not build indexes"))
        result = await backfill(client, "titles", journal)
        assert result["accepted"] == 1 and result["stage"] == "submitted"
        client.retrieve.assert_not_awaited()
        client.count.assert_not_awaited()
        client.create_payload_index.assert_not_awaited()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_verification_is_explicit_and_marks_verified_only_after_application(tmp_path):
    client = AsyncQdrantClient(":memory:")
    journal = tmp_path / "verify.json"
    try:
        await client.create_collection("titles", vectors_config={})
        await client.upsert("titles", points=[models.PointStruct(id=1, vector={}, payload={"title": "Title"})])
        submitted = await backfill(client, "titles", journal)
        assert submitted["stage"] == "submitted"
        # 本地 Qdrant 不提供更新队列，用外部元信息边界声明已经清空；计数仍走实际引擎。
        client.get_collection = AsyncMock(return_value=SimpleNamespace(points_count=1, update_queue=SimpleNamespace(length=0, deferred_points=None)))
        result = await verify(client, "titles", journal)
        assert result["stage"] == "verified"
        assert json.loads(journal.read_text())["stage"] == "verified"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_verification_with_pending_updates_fails_once_without_polling(tmp_path):
    journal = tmp_path / "pending.json"
    journal.write_text(json.dumps({"collection": "titles", "cursor": None, "accepted": 1, "stage": "submitted"}))
    client = SimpleNamespace(
        get_collection=AsyncMock(return_value=SimpleNamespace(update_queue=SimpleNamespace(length=1))),
        count=AsyncMock(),
    )
    with pytest.raises(RuntimeError, match="pending updates"):
        await verify(client, "titles", journal)
    client.get_collection.assert_awaited_once_with("titles")
    client.count.assert_not_awaited()
    assert json.loads(journal.read_text())["stage"] == "submitted"


@pytest.mark.asyncio
async def test_existing_confirmed_checkpoint_resumes_as_accepted_prefix(tmp_path):
    client = AsyncQdrantClient(":memory:")
    journal = tmp_path / "legacy.json"
    try:
        await client.create_collection("titles", vectors_config={})
        await client.upsert("titles", points=[
            models.PointStruct(id=1, vector={}, payload={"title": "First", "normalized_title": "first"}),
            models.PointStruct(id=2, vector={}, payload={"title": "Second"}),
        ])
        journal.write_text(json.dumps({"collection": "titles", "cursor": 2, "updated": 1, "complete": False}))
        result = await backfill(client, "titles", journal, resume=True)
        assert result["accepted"] == 2 and result["stage"] == "submitted"
        assert "updated" not in result and "complete" not in result
    finally:
        await client.close()

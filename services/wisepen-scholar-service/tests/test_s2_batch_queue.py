"""真实 Redis 验证跨实例合流、回执和双通道发射，不访问真实 S2。"""

import asyncio
import json
import multiprocessing
import os
import time
from contextlib import asynccontextmanager
from itertools import pairwise
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from loguru import logger
from redis.asyncio import Redis

from scholar.core.providers.s2.transport import S2Transport, s2_queued_ahead
from scholar.domain.errors import ProviderBusy, ProviderError


@pytest.fixture
def s2_logs():
    messages = []
    handler = logger.add(messages.append, level="WARNING", format="{message}")
    try:
        yield messages
    finally:
        logger.remove(handler)


@pytest_asyncio.fixture
async def boundary():
    url = os.environ.get("SCHOLAR_TEST_REDIS_URL")
    if not url:
        pytest.skip("set SCHOLAR_TEST_REDIS_URL for real Redis acceptance")
    namespace = f"scholar:test:s2:{uuid4().hex}"
    first, second = [Redis.from_url(url, socket_timeout=5) for _ in range(2)]
    await first.ping()
    try:
        yield first, second, namespace
    finally:
        keys = [key async for key in first.scan_iter(match=f"{namespace}:*")]
        if keys:
            await first.delete(*keys)
        await first.aclose()
        await second.aclose()


@asynccontextmanager
async def transports(boundary, handler, **options):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        config = dict(namespace=boundary[2], nokey_interval=0.12, key_interval=0.12,
                      request_timeout=4, max_queued_ahead=3)
        config.update(options)
        clients = [S2Transport(http, redis, "platform", **config) for redis in boundary[:2]]
        try:
            yield clients
        finally:
            await asyncio.gather(*(client.aclose() for client in clients))


async def lookup(client, ids, **kwargs):
    return await client.request("POST", "graph/v1/paper/batch",
                                params={"fields": "paperId,title"}, json={"ids": ids}, **kwargs)


async def wait_until(predicate):
    async with asyncio.timeout(4):
        while not await predicate():
            await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_cross_client_merge_preserves_positions_duplicates_and_null(boundary):
    sent = []

    async def handle(request):
        ids = json.loads(request.content)["ids"]
        sent.append((request, ids))
        return httpx.Response(200, json=[None if item == "C" else {"paperId": item} for item in ids])

    async with transports(boundary, handle) as (one, two):
        first, second = await asyncio.gather(lookup(one, ["A", "B", "A"]), lookup(two, ["B", "C"]))
    assert len(sent) == 1
    assert set(sent[0][1]) == {"A", "B", "C"}
    assert "x-api-key" not in sent[0][0].headers
    assert first == [{"paperId": "A"}, {"paperId": "B"}, {"paperId": "A"}]
    assert second == [{"paperId": "B"}, None]


@pytest.mark.parametrize("offline", [False, True])
@pytest.mark.asyncio
async def test_full_batch_waits_and_slow_response_does_not_block_next_launch(boundary, offline):
    sent, release = [], asyncio.Event()

    async def handle(request):
        ids = json.loads(request.content)["ids"]
        sent.append((time.monotonic(), ids))
        if "A" in ids:
            await release.wait()
        return httpx.Response(200, json=ids)

    async with transports(boundary, handle, batch_max_ids=2, offline=offline,
                          max_queued_ahead=0 if offline else 3) as (one, two):
        before = time.monotonic()
        a = asyncio.create_task(lookup(one, ["A", "B", "A", "C"]))
        b = asyncio.create_task(lookup(two, ["D"]))
        try:
            async def two_sent():
                return len(sent) == 2
            await wait_until(two_sent)
            assert not a.done()
            assert sent[0][0] - before >= 0.11
            assert sent[1][0] - sent[0][0] >= 0.115
            assert all(len(ids) <= 2 for _, ids in sent)
            release.set()
            assert await a == ["A", "B", "A", "C"]
            assert await b == ["D"]
        finally:
            release.set()
            await asyncio.gather(a, b, return_exceptions=True)


@pytest.mark.asyncio
async def test_429_fallback_has_independent_pacing_and_preserves_batch(boundary, s2_logs):
    sent = []

    async def handle(request):
        ids = json.loads(request.content)["ids"]
        key = request.headers.get("x-api-key")
        sent.append((key, time.monotonic(), ids))
        return httpx.Response(429, json={"error": "limited"}) if key is None else httpx.Response(200, json=ids)

    async with transports(boundary, handle, batch_max_ids=1, key_interval=0.2) as (one, two):
        assert await asyncio.gather(lookup(one, ["A", "B"]), lookup(two, ["C"])) == [["A", "B"], ["C"]]
    public = [entry for entry in sent if entry[0] is None]
    keyed = [entry for entry in sent if entry[0] == "platform"]
    assert [entry[2] for entry in public] == [entry[2] for entry in keyed]
    assert all(after[1] - before[1] >= 0.115 for before, after in pairwise(public))
    assert all(after[1] - before[1] >= 0.195 for before, after in pairwise(keyed))
    assert len(s2_logs) == len(keyed)
    assert all("scope=online" in message and "status_code=429" in message for message in s2_logs)


@pytest.mark.parametrize("failure", [400, 403, 503, "timeout", "malformed"])
@pytest.mark.asyncio
async def test_only_429_uses_key(boundary, failure):
    sent = []

    async def handle(request):
        sent.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("temporary network timeout", request=request)
        if failure == "malformed":
            return httpx.Response(200, json=[])
        return httpx.Response(failure, json={"error": "bad response"})

    async with transports(boundary, handle) as (one, _):
        with pytest.raises((httpx.HTTPStatusError, ProviderError)):
            await lookup(one, ["A"])
    assert len(sent) == 1
    assert "x-api-key" not in sent[0].headers


@pytest.mark.asyncio
async def test_failed_fallback_propagates_http_status(boundary):
    async def handle(request):
        return httpx.Response(429 if "x-api-key" not in request.headers else 503, text="failed")

    async with transports(boundary, handle) as (one, _):
        with pytest.raises(httpx.HTTPStatusError) as raised:
            await lookup(one, ["A"])
    assert raised.value.response.status_code == 503
    assert raised.value.response.text == "failed"


@pytest.mark.asyncio
async def test_admission_counts_batches_and_rejection_is_atomic(boundary):
    async def handle(request):
        return httpx.Response(200, json=json.loads(request.content)["ids"])

    async with transports(boundary, handle, batch_max_ids=2, nokey_interval=0.5) as (one, two):
        calls = [asyncio.create_task(lookup(one, [str(i)])) for i in range(8)]
        duplicate = None
        try:
            async def four_batches():
                return await boundary[0].llen(f"{boundary[2]}:online:nokey:queue") == 4
            await wait_until(four_batches)
            duplicate = asyncio.create_task(lookup(two, ["0"]))
            with pytest.raises(ProviderBusy) as raised:
                await lookup(two, ["0", "overflow"])
            assert raised.value.queued_ahead == 4
            assert await duplicate == ["0"]
            assert await asyncio.gather(*calls) == [[str(i)] for i in range(8)]
        finally:
            for call in [*calls, *([duplicate] if duplicate is not None else [])]:
                call.cancel()
            await asyncio.gather(*calls, *([duplicate] if duplicate is not None else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_call_does_not_damage_shared_batch(boundary):
    entered, release = asyncio.Event(), asyncio.Event()
    sent = []

    async def handle(request):
        ids = json.loads(request.content)["ids"]
        sent.append(ids)
        entered.set()
        await release.wait()
        return httpx.Response(200, json=ids)

    async with transports(boundary, handle) as (one, two):
        a = asyncio.create_task(lookup(one, ["A", "B"]))
        b = asyncio.create_task(lookup(two, ["B", "C"]))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            a.cancel()
            with pytest.raises(asyncio.CancelledError):
                await a
            release.set()
            assert await b == ["B", "C"]
            assert len(sent) == 1
        finally:
            release.set()
            await asyncio.gather(a, b, return_exceptions=True)


@pytest.mark.asyncio
async def test_all_cancelled_before_departure_sends_nothing(boundary):
    sent = []

    async def handle(request):
        sent.append(request)
        return httpx.Response(200, json=[])

    async with transports(boundary, handle, nokey_interval=0.3) as (one, _):
        call = asyncio.create_task(lookup(one, ["A"]))
        async def submitted():
            return await boundary[0].llen(f"{boundary[2]}:online:nokey:queue") == 1
        await wait_until(submitted)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        await asyncio.sleep(0.35)
    assert sent == []


@pytest.mark.asyncio
async def test_offline_yields_to_online_key_and_shares_anchor(boundary):
    entered, fail_public = asyncio.Event(), asyncio.Event()
    sent = []

    async def handle(request):
        ids = json.loads(request.content)["ids"]
        if "x-api-key" not in request.headers:
            entered.set()
            await fail_public.wait()
            return httpx.Response(429)
        sent.append((ids, time.monotonic()))
        return httpx.Response(200, json=ids)

    async with transports(boundary, handle) as (online, _):
        offline = S2Transport(online.http, boundary[1], "platform", namespace=boundary[2],
                              offline=True, nokey_interval=0.12, key_interval=0.12)
        seconds, micros = await boundary[0].time()
        await boundary[0].set(f"{boundary[2]}:send-permit", seconds * 1000 + micros // 1000 + 300)
        foreground = asyncio.create_task(lookup(online, ["online"]))
        await asyncio.wait_for(entered.wait(), 3)
        background = asyncio.create_task(lookup(offline, ["offline"]))
        fail_public.set()
        try:
            assert await asyncio.gather(foreground, background) == [["online"], ["offline"]]
        finally:
            await offline.aclose()
    assert [ids for ids, _ in sent] == [["online"], ["offline"]]
    assert sent[1][1] - sent[0][1] >= 0.115


@pytest.mark.asyncio
async def test_queue_snapshot_is_set_in_original_context(boundary):
    async def handle(request):
        return httpx.Response(200, json=json.loads(request.content)["ids"])

    async with transports(boundary, handle) as (one, _):
        s2_queued_ahead.set(None)
        assert await lookup(one, ["A"]) == ["A"]
        assert s2_queued_ahead.get() == 0


def process_lookup(url, namespace, ids, barrier, output):
    """每个子进程拥有独立的事件循环、HTTP client 和回执 Future。"""
    async def run():
        redis = Redis.from_url(url, socket_timeout=5)

        async def handle(request):
            payload = json.loads(request.content)["ids"]
            output.put(("sent", payload))
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            client = S2Transport(http, redis, "platform", namespace=namespace,
                                  nokey_interval=0.5, key_interval=0.12, request_timeout=8)
            try:
                output.put(("result", ids, await lookup(client, ids)))
            finally:
                await client.aclose()
                await redis.aclose()

    barrier.wait(timeout=10)
    asyncio.run(run())


@pytest.mark.asyncio
async def test_two_real_processes_merge_and_receive_their_own_results(boundary):
    context = multiprocessing.get_context("spawn")
    barrier, output = context.Barrier(2), context.Queue()
    processes = [context.Process(target=process_lookup,
                                args=(os.environ["SCHOLAR_TEST_REDIS_URL"], boundary[2], ids, barrier, output))
                 for ids in (["A", "B", "A"], ["B", "C"])]
    for process in processes:
        process.start()
    try:
        messages = [await asyncio.to_thread(output.get, True, 15) for _ in range(3)]
        assert len([message for message in messages if message[0] == "sent"]) == 1
        assert set(next(message[1] for message in messages if message[0] == "sent")) == {"A", "B", "C"}
        assert all(message[1] == message[2] for message in messages if message[0] == "result")
        for process in processes:
            await asyncio.to_thread(process.join, 10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 5)
        output.close()


@pytest.mark.asyncio
async def test_leader_exit_preserves_next_departure_anchor(boundary):
    sent = []

    async def handle(request):
        sent.append(time.monotonic())
        return httpx.Response(200, json=json.loads(request.content)["ids"])

    async with transports(boundary, handle) as (one, two):
        assert await lookup(one, ["A"]) == ["A"]
        await one.aclose()
        assert await lookup(two, ["B"]) == ["B"]
    assert sent[1] - sent[0] >= 0.115


@pytest.mark.asyncio
async def test_late_arrival_joins_next_bus_without_its_own_window(boundary):
    sent = []

    async def handle(request):
        ids = json.loads(request.content)["ids"]
        sent.append(ids)
        return httpx.Response(200, json=ids)

    async with transports(boundary, handle, nokey_interval=0.4) as (one, two):
        assert await lookup(one, ["warmup"]) == ["warmup"]
        early = asyncio.create_task(lookup(one, ["early"]))
        await asyncio.sleep(0.15)
        late = asyncio.create_task(lookup(two, ["late"]))
        assert await asyncio.gather(early, late) == [["early"], ["late"]]
    assert sent == [["warmup"], ["early", "late"]]


@pytest.mark.asyncio
async def test_one_failed_fragment_fails_only_its_own_call(boundary):
    async def handle(request):
        ids = json.loads(request.content)["ids"]
        return httpx.Response(503, text="failed fragment") if "B" in ids else httpx.Response(200, json=ids)

    async with transports(boundary, handle, batch_max_ids=1) as (one, two):
        broken = asyncio.create_task(lookup(one, ["A", "B"]))
        intact = asyncio.create_task(lookup(two, ["A"]))
        assert await intact == ["A"]
        with pytest.raises(httpx.HTTPStatusError) as raised:
            await broken
        assert raised.value.response.status_code == 503


@pytest.mark.asyncio
async def test_request_budget_cancels_fallback_wait_without_sending_key(boundary):
    sent = []

    async def handle(request):
        sent.append(request.headers.get("x-api-key"))
        return httpx.Response(429)

    async with transports(boundary, handle, request_timeout=0.35) as (one, _):
        seconds, micros = await boundary[0].time()
        await boundary[0].set(f"{boundary[2]}:send-permit", seconds * 1000 + micros // 1000 + 1000)
        with pytest.raises(TimeoutError):
            await lookup(one, ["A"])
        await asyncio.sleep(0.15)
    assert sent == [None]


@pytest.mark.asyncio
async def test_scheduler_stall_does_not_create_catch_up_burst(boundary, monkeypatch):
    sent = []

    async def handle(request):
        sent.append(time.monotonic())
        return httpx.Response(200, json=json.loads(request.content)["ids"])

    async with transports(boundary, handle, batch_max_ids=1) as (one, two):
        original_build = one.http.build_request
        stalled = False

        def build(*args, **kwargs):
            nonlocal stalled
            if not stalled:
                stalled = True
                time.sleep(0.2)
            return original_build(*args, **kwargs)

        monkeypatch.setattr(one.http, "build_request", build)
        assert await asyncio.gather(lookup(one, ["A", "B"]), lookup(two, ["C"])) == [["A", "B"], ["C"]]
    assert all(after - before >= 0.115 for before, after in pairwise(sent))




@pytest.mark.parametrize("nokey_status", [200, 429])
@pytest.mark.asyncio
async def test_offline_sync_uses_dual_channels_and_preserves_vectors(boundary, nokey_status, s2_logs):
    from offline.s2_api import S2SyncClient
    from scholar.domain.models.identifiers import parse_identifier

    sent = []

    async def handle(request):
        key = request.headers.get("x-api-key")
        sent.append(key)
        assert "embedding.specter_v2" in request.url.params["fields"]
        if key is None and nokey_status == 429:
            return httpx.Response(429)
        return httpx.Response(200, json=[{
            "paperId": "a" * 40, "externalIds": {"ArXiv": "2401.00001"},
            "embedding": {"model": "specter_v2", "vector": [0.25] * 768},
        }])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with S2Transport(http, boundary[0], "platform", namespace=boundary[2],
                               offline=True, nokey_interval=0.12, key_interval=0.12) as transport:
            result = await S2SyncClient(transport).lookup([parse_identifier("2401.00001")])
    assert sent == ([None] if nokey_status == 200 else [None, "platform"])
    assert result[0].vector == [0.25] * 768
    assert parse_identifier("2401.00001") in result[0].identifiers
    assert len(s2_logs) == (1 if nokey_status == 429 else 0)
    for message in s2_logs:
        assert "scope=offline" in message
        assert "from_channel=NOKEY" in message and "to_channel=KEY" in message
        assert "id_count=1" in message and "key_interval_seconds=0.12" in message
        assert "platform" not in message


@pytest.mark.asyncio
async def test_offline_empty_fragment_does_not_hide_other_found_papers(boundary):
    from offline.s2_api import S2SyncClient
    from scholar.domain.models.identifiers import parse_identifier

    async def handle(request):
        assert "x-api-key" not in request.headers
        ids = json.loads(request.content)["ids"]
        if ids == ["arxiv:2401.00001"]:
            return httpx.Response(400, json={"error": "No valid paper ids given"})
        return httpx.Response(200, json=[{
            "paperId": "b" * 40, "externalIds": {"ArXiv": "2401.00002"},
            "embedding": {"model": "specter_v2", "vector": [0.25] * 768},
        }])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with S2Transport(http, boundary[0], "platform", namespace=boundary[2],
                               offline=True, batch_max_ids=1, nokey_interval=0.12) as transport:
            result = await S2SyncClient(transport).lookup([
                parse_identifier("2401.00001"), parse_identifier("2401.00002"),
            ])
    assert result[0] is None
    assert result[1] is not None
    assert result[1].vector == [0.25] * 768

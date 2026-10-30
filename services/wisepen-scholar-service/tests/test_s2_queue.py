"""在独立 Redis 命名空间验证跨客户端 FIFO，不访问 S2 或现有任务键。"""
from policy_fakes import make_policy_store

from pdf_fakes import make_pdf_service

from dataclasses import replace
from scholar.core.providers import PROVIDER_POLICIES

import asyncio
import hashlib
import os
import time
from itertools import pairwise
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from redis.asyncio import Redis
from scholar.application.lookup_runner import LookupBinding, LookupRunner
from scholar.application.lookup import (
    PaperLookup,
    PaperLookupService,
)
from scholar.core.storage.redis.paper_cache import RedisPaperLookupCache
from scholar.domain.errors import ProviderBusy
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.identifiers import IdentifierKind, parse_identifier
from scholar.domain.models.paper import Paper
from scholar.domain.models.source_execution import SourceExecutionStatus
from scholar.core.providers import S2Provider, S2Transport
from scholar.core.providers.s2 import has_signals as s2_signals


@pytest_asyncio.fixture
async def queue_boundary():
    url = os.environ.get("SCHOLAR_TEST_REDIS_URL")
    if not url:
        pytest.skip("set SCHOLAR_TEST_REDIS_URL to run real Redis queue acceptance")
    namespace = f"scholar:test:s2:{uuid4().hex}"
    first, second = [Redis.from_url(url, socket_timeout=5) for _ in range(2)]
    await first.ping()
    try:
        yield first, second, namespace
    finally:
        # Only this fixture's exact keys; never scan or delete the service namespace.
        await first.delete(
            *(
                f"{prefix}:{suffix}"
                for prefix in [namespace, *(f"{namespace}:byok:{hashlib.sha256(key.encode()).hexdigest()}" for key in ("user-a", "user-b"))]
                for suffix in ("queue", "leases", "sequence", "send-permit")
            )
        )
        await first.aclose()
        await second.aclose()


async def wait_count(redis, namespace, count):
    async with asyncio.timeout(5):
        while await redis.zcard(f"{namespace}:queue") != count:
            await asyncio.sleep(0.01)


async def seed_waiters(redis, namespace, count, *, expired=False):
    seconds, micros = await redis.time()
    now = seconds * 1000 + micros // 1000
    members = {f"existing-{i}": i for i in range(count)}
    await redis.zadd(f"{namespace}:queue", members)
    await redis.zadd(
        f"{namespace}:leases",
        {member: now - 1 if expired else now + 15000 for member in members},
    )
    await redis.set(f"{namespace}:sequence", count)


@pytest.mark.asyncio
async def test_fifo_across_clients_and_offline_shares_one_second_quota(queue_boundary):
    first, second, namespace = queue_boundary
    sent = []

    async def handle(request):
        sent.append((request.url.path, time.monotonic()))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        online = S2Transport(
            http, first, "key", namespace=namespace, max_queued_ahead=5
        )
        offline = S2Transport(http, second, "key", namespace=namespace)
        # 用显式占位协调入队，避免远程 Redis 往返耗时超过一秒导致观测竞争。
        await seed_waiters(first, namespace, 1)
        tasks = []
        try:
            for i, transport in enumerate([online, offline, online]):
                tasks.append(asyncio.create_task(transport.request("GET", f"test/{i}")))
                await wait_count(first, namespace, i + 2)
            await first.zrem(f"{namespace}:queue", "existing-0")
            await first.zrem(f"{namespace}:leases", "existing-0")
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    assert [path for path, _ in sent] == ["/test/0", "/test/1", "/test/2"]
    assert all(after[1] - before[1] >= 0.95 for before, after in pairwise(sent))
    assert await first.zcard(f"{namespace}:queue") == 0
    assert await first.zcard(f"{namespace}:leases") == 0


@pytest.mark.asyncio
async def test_five_ahead_allowed_six_skipped_and_offline_not_rejected(queue_boundary):
    first, second, namespace = queue_boundary
    await seed_waiters(first, namespace, 5)

    async def handle(request):
        raise AssertionError("waiting or rejected task must not send")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        online = S2Transport(
            http, first, "key", namespace=namespace, max_queued_ahead=5
        )
        allowed = asyncio.create_task(online.request("GET", "allowed"))
        offline = None
        try:
            await wait_count(first, namespace, 6)
            with pytest.raises(ProviderBusy) as raised:
                await online.request("GET", "rejected")
            assert raised.value.queued_ahead == 6
            assert await first.zcard(f"{namespace}:queue") == 6
            offline = asyncio.create_task(
                S2Transport(http, second, "key", namespace=namespace).request(
                    "GET", "offline"
                )
            )
            await wait_count(first, namespace, 7)
        finally:
            allowed.cancel()
            if offline is not None:
                offline.cancel()
            await asyncio.gather(
                allowed,
                *([offline] if offline is not None else []),
                return_exceptions=True,
            )
    assert await first.zcard(f"{namespace}:queue") == 5
    assert await first.zcard(f"{namespace}:leases") == 5


@pytest.mark.asyncio
async def test_timeout_cleans_waiter_and_expired_process_lease_is_pruned(
    queue_boundary,
):
    first, _, namespace = queue_boundary
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    ) as http:
        await seed_waiters(first, namespace, 1)
        transport = S2Transport(
            http, first, "key", namespace=namespace, max_queued_ahead=5
        )
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await transport.request("GET", "timeout")
        assert await first.zcard(f"{namespace}:queue") == 1
        assert await first.zcard(f"{namespace}:leases") == 1
        await first.zadd(f"{namespace}:leases", {"existing-0": 0})
        await transport.request("GET", "after-expired-process")
    assert await first.zcard(f"{namespace}:queue") == 0
    assert await first.zcard(f"{namespace}:leases") == 0


class FallbackProvider:
    async def lookup(self, ids, *, api_key=None):
        return [
            PaperLookupMatch(
                [
                    Paper(
                        arxiv_id=identity.value,
                        doi=None,
                        title="Fallback paper",
                        abstract=None,
                        authors=[],
                        publication_date=None,
                        year=None,
                        url="https://example.test/paper",
                        pdf_url=None,
                        source="openalex",
                        metadata={"s2": {"citation_count": 0}},
                    )
                ]
            )
            for identity in ids
        ]


@pytest.mark.asyncio
async def test_busy_is_not_executed_and_openalex_fallback_is_complete(queue_boundary):
    first, _, namespace = queue_boundary
    await seed_waiters(first, namespace, 6)
    identity = parse_identifier("2401.00001")

    async def handle(request):
        raise AssertionError("busy S2 must not send")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        runner = LookupRunner(
            {
                "s2": LookupBinding(S2Provider(
                        S2Transport(
                            http, first, "key", namespace=namespace, max_queued_ahead=5
                        )
                    ), None, replace(PROVIDER_POLICIES["s2"], identifier_kinds=[IdentifierKind.ARXIV], has_signals=s2_signals)),
                "openalex": LookupBinding(FallbackProvider(), None, replace(PROVIDER_POLICIES["openalex"], identifier_kinds=[IdentifierKind.ARXIV], has_signals=s2_signals)),
            }
        )
        result = await PaperLookupService(runner, pdf=make_pdf_service(), policies=make_policy_store()).lookup(
            PaperLookup([identity.value])
        )
    execution = next(e for e in result.execution if e.source == "s2")
    assert execution.status == SourceExecutionStatus.NOT_EXECUTED
    assert execution.queued_ahead == 6
    assert "queue busy" in execution.error
    assert result.items[0].error is None


@pytest.mark.asyncio
async def test_cache_and_identical_inflight_batch_use_one_queue_task(queue_boundary):
    first, _, namespace = queue_boundary
    sent = []

    async def handle(request):
        sent.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "paperId": "a" * 40,
                    "url": "https://www.semanticscholar.org/paper/" + "a" * 40,
                    "externalIds": {"ArXiv": "2401.00001"},
                    "citationCount": 0,
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        cache = RedisPaperLookupCache(first, namespace)
        runner = LookupRunner(
            {
                "s2": LookupBinding(S2Provider(
                        S2Transport(
                            http, first, "key", namespace=namespace, max_queued_ahead=5
                        )
                    ), cache, replace(PROVIDER_POLICIES["s2"], identifier_kinds=[IdentifierKind.ARXIV], has_signals=s2_signals))
            }
        )
        service = PaperLookupService(runner, pdf=make_pdf_service(), policies=make_policy_store())
        query = PaperLookup(["2401.00001", "2401.00001"])
        seconds, micros = await first.time()
        await first.set(f"{namespace}:send-permit", seconds * 1000 + micros // 1000)
        one = asyncio.create_task(service.lookup(query))
        two = asyncio.create_task(service.lookup(query))
        try:
            await wait_count(first, namespace, 1)
            await asyncio.sleep(0.05)
            assert await first.zcard(f"{namespace}:queue") == 1
            results = await asyncio.gather(one, two)
            assert all(
                next(e for e in r.execution if e.source == "s2").queued_ahead == 0
                for r in results
            )
            cached = await service.lookup(query)
            execution = next(e for e in cached.execution if e.source == "s2")
            assert execution.cache_hit and execution.queued_ahead is None
            assert len(sent) == 1
        finally:
            one.cancel()
            two.cancel()
            await asyncio.gather(one, two, return_exceptions=True)
            await cache.invalidate([parse_identifier("2401.00001")])


@pytest.mark.asyncio
async def test_byok_fifo_is_per_key_and_different_keys_send_independently(queue_boundary):
    first, second, namespace = queue_boundary
    user_queue = f"{namespace}:byok:{hashlib.sha256(b'user-a').hexdigest()}"
    await seed_waiters(first, user_queue, 1)
    sent = []

    async def handle(request):
        sent.append((request.url.path, request.headers["x-api-key"], time.monotonic()))
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        one = S2Transport(http, first, "platform", namespace=namespace, max_queued_ahead=0)
        two = S2Transport(http, second, "platform", namespace=namespace, max_queued_ahead=0)
        tasks = []
        try:
            tasks.append(asyncio.create_task(one.request("GET", "a1", api_key="user-a")))
            await wait_count(first, user_queue, 2)
            tasks.append(asyncio.create_task(two.request("GET", "a2", api_key="user-a")))
            await wait_count(first, user_queue, 3)
            await two.request("GET", "b", api_key="user-b")
            assert sent[0][:2] == ("/b", "user-b")
            await first.zrem(f"{user_queue}:queue", "existing-0")
            await first.zrem(f"{user_queue}:leases", "existing-0")
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    own = [entry for entry in sent if entry[1] == "user-a"]
    assert [entry[0] for entry in own] == ["/a1", "/a2"]
    assert own[1][2] - own[0][2] >= 0.95
    assert await first.zcard(f"{user_queue}:queue") == 0


@pytest.mark.asyncio
async def test_platform_threshold_applies_to_same_key_but_byok_waits_until_cancelled(queue_boundary):
    first, _, namespace = queue_boundary
    own = f"{namespace}:byok:{hashlib.sha256(b'user-a').hexdigest()}"
    await seed_waiters(first, namespace, 6)
    await seed_waiters(first, own, 6)
    sent = []

    async def handle(request):
        sent.append(request.headers["x-api-key"])
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        transport = S2Transport(http, first, "platform", namespace=namespace, max_queued_ahead=5)
        with pytest.raises(ProviderBusy):
            await transport.request("GET", "platform", api_key="platform")
        waiting = asyncio.create_task(transport.request("GET", "byok", api_key="user-a"))
        try:
            await wait_count(first, own, 7)
            await transport.request("GET", "independent", api_key="user-b")
            assert sent == ["user-b"]
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
    assert await first.zcard(f"{own}:queue") == 6
    assert await first.zcard(f"{own}:leases") == 6


@pytest.mark.asyncio
async def test_byok_inflight_isolated_but_completed_papers_shared_across_keys(queue_boundary):
    first, _, namespace = queue_boundary
    sent = []
    started, release = asyncio.Event(), asyncio.Event()

    async def handle(request):
        sent.append(request.headers["x-api-key"])
        if len(sent) == 2:
            started.set()
        await release.wait()
        return httpx.Response(200, json=[{
            "paperId": "a" * 40, "url": "https://s2.test/paper",
            "externalIds": {"ArXiv": "2401.00001"}, "citationCount": 0,
        }])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        cache = RedisPaperLookupCache(first, namespace)
        runner = LookupRunner({"s2": LookupBinding(
            S2Provider(S2Transport(http, first, "platform", namespace=namespace, max_queued_ahead=5)),
            cache, PROVIDER_POLICIES["s2"],
        )})
        service = PaperLookupService(runner, pdf=make_pdf_service(), platform_credentials={"s2": "platform"}, policies=make_policy_store())
        query = PaperLookup(["2401.00001", "2401.00001"])
        tasks = [asyncio.create_task(service.lookup(query, credentials={"s2": key})) for key in ("user-a", "user-a", "user-b")]
        try:
            await asyncio.wait_for(started.wait(), 3)
            assert sorted(sent) == ["user-a", "user-b"]
            release.set()
            await asyncio.gather(*tasks)
            cached = await service.lookup(query)
            assert cached.execution[0].cache_hit is True
            assert len(sent) == 2
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await cache.invalidate([parse_identifier("2401.00001")])

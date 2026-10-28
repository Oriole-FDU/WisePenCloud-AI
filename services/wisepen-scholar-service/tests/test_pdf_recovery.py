"""PDF 补救保护已有阅读入口、来源事实以及按 DOI 复用的正负结果。"""

import asyncio
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest
from pdf_fakes import MemoryPdfCache, make_pdf_service
from scholar.domain.models.paper import Paper
from scholar.domain.models.source_execution import SourceExecutionStatus
from scholar.core.providers import UnpaywallProvider


def paper(**changes):
    value = Paper(None, "10.1234/example", "Research", "Abstract", ["Author"], None, 2026,
                  "https://publisher.test/article", None, "s2")
    return replace(value, **changes)


@pytest.mark.asyncio
@pytest.mark.parametrize(("changes", "expected"), [
    ({"pdf_url": "https://top.test/pdf", "metadata": {"s2": {"open_access_pdf": "https://other.test/pdf"}}}, "https://top.test/pdf"),
    ({"metadata": {"s2": {"open_access_pdf": "https://s2.test/pdf"}, "openalex": {"best_oa_location": {"pdf_url": "https://oa.test/pdf"}}}}, "https://s2.test/pdf"),
    ({"metadata": {"openalex": {"best_oa_location": {"pdf_url": "https://oa.test/pdf"}}}}, "https://oa.test/pdf"),
    ({"arxiv_id": "1706.03762"}, "https://arxiv.org/pdf/1706.03762"),
    ({"doi": None}, None),
])
async def test_existing_pdf_and_arxiv_identity_skip_external_recovery(changes, expected):
    source = paper(**changes)
    original = deepcopy(source)
    provider = AsyncMock()
    recovered, execution = await make_pdf_service(provider).recover([source])

    assert recovered[0].pdf_url == expected
    assert source == original
    assert execution is None
    provider.recover.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "best", "locations", "expected"), [
    (200, {"url_for_pdf": "https://publisher.test/pdf"}, [{"url_for_pdf": "https://repo.test/pdf"}], "https://publisher.test/pdf"),
    (200, {"url_for_pdf": None, "url": "https://publisher.test/html"}, [{"url_for_pdf": None}, {"url_for_pdf": "https://repo.test/pdf"}], "https://repo.test/pdf"),
    (200, {"url_for_pdf": None, "url": "https://publisher.test/html"}, [], None),
    (404, None, [], None),
])
async def test_unpaywall_uses_only_pdf_fields_and_caches_positive_and_negative_results(status, best, locations, expected):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json={"is_oa": False, "best_oa_location": best, "oa_locations": locations})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        cache = MemoryPdfCache()
        service = make_pdf_service(UnpaywallProvider(http, "jzsun24@m.fudan.edu.cn"), cache)
        first, execution = await service.recover([paper(doi="10.1234/EXAMPLE"), paper()])
        repeated, cached = await service.recover([paper()])

    assert [p.pdf_url for p in first] == [expected, expected]
    assert repeated[0].pdf_url == expected
    assert execution.status == SourceExecutionStatus.COMPLETED
    assert cached.cache_hit is True
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/v2/10.1234/example"
    assert requests[0].url.params["email"] == "jzsun24@m.fudan.edu.cn"
    assert cache.writes == [("10.1234/example", expected, 86400 if expected else 3600)]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["http", "timeout"])
async def test_unpaywall_errors_preserve_papers_and_are_not_negative_cached(failure):
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("/failed"):
            if failure == "timeout":
                raise httpx.ReadTimeout("upstream timeout", request=request)
            return httpx.Response(429)
        return httpx.Response(200, json={"best_oa_location": {"url_for_pdf": "https://repo.test/pdf"}, "oa_locations": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        cache = MemoryPdfCache()
        service = make_pdf_service(UnpaywallProvider(http, "jzsun24@m.fudan.edu.cn"), cache)
        source = [paper(), paper(doi="10.1234/failed")]
        original = deepcopy(source)
        recovered, execution = await service.recover(source)
        _, failed = await service.recover([source[1]])

    assert recovered[0].pdf_url == "https://repo.test/pdf"
    assert recovered[1].pdf_url is None
    assert execution.status == SourceExecutionStatus.PARTIAL
    assert execution.error
    assert failed.status == SourceExecutionStatus.FAILED
    assert "10.1234/failed" not in cache.values
    assert len(requests) == 3
    assert source == original


@pytest.mark.asyncio
async def test_unpaywall_bounds_concurrency_and_cancels_pending_http():
    active, peak = 0, 0
    started = asyncio.Event()

    async def handle(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 4:
            started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        service = make_pdf_service(UnpaywallProvider(http, "jzsun24@m.fudan.edu.cn"))
        task = asyncio.create_task(service.recover([paper(doi=f"10.1234/{i}") for i in range(8)]))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert peak == 4
    assert active == 0

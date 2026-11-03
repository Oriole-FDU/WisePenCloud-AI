"""标题匹配 HTTP 边界保留逐项失败，同时限制批量输入并透传用户凭证。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI

from scholar.api.endpoints import papers
from scholar.application.limitations import ResultLimitation, LimitationReason
from scholar.application.match import MatchItem, MatchResult, MatchStatus
from scholar.container import container


@pytest.mark.asyncio
async def test_match_http_keeps_all_rate_limited_items_in_success_response():
    service = SimpleNamespace(match=AsyncMock(return_value=MatchResult([
        MatchItem("A", MatchStatus.FAILED, limitations=[ResultLimitation("crossref", LimitationReason.RATE_LIMITED)]),
        MatchItem("B", MatchStatus.FAILED, limitations=[ResultLimitation("crossref", LimitationReason.RATE_LIMITED)]),
    ])))
    app = FastAPI()
    app.include_router(papers.router)
    with container.paper_match_service.override(providers.Object(service)), container.config.SCHOLAR_REQUEST_TIMEOUT_SECONDS.override(25):
        container.wire(modules=[papers])
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/papers/match", json={"titles": [" A ", "B"], "field": "medicine"}, headers={"X-S2-Api-Key": "user-key"})
                assert response.status_code == 200
                assert [item["status"] for item in response.json()["data"]["items"]] == ["failed", "failed"]
                data = response.json()["data"]
                assert set(data) == {"items"}
                assert data["items"][0]["limitations"] == [{"source": "crossref", "reason": "rate_limited"}]
                assert "error" not in data["items"][0]
                query = service.match.await_args.args[0]
                assert query.titles == ["A", "B"] and query.field == "medicine"
                assert service.match.await_args.kwargs == {"timeout": 25, "credentials": {"s2": "user-key"}}
                for titles in ([], [" "], ["A"] * 21):
                    invalid = await client.post("/papers/match", json={"titles": titles})
                    assert invalid.status_code == 422
        finally:
            container.unwire()

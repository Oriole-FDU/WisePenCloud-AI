"""RPC 错误保留上游业务码和完整原因，且限流不触发 5xx 重试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 502, 504])
async def test_rpc_preserves_http_error_envelope_after_retries(monkeypatch, status):
    message = "qdrant.search: " + "query timed out; " * 20
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"code": status, "msg": message, "data": None})

    http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    discovery = SimpleNamespace(pick=AsyncMock(return_value=SimpleNamespace(ip="127.0.0.1", port=19916)))
    with monkeypatch.context() as patch:
        patch.setattr("common.http.rpc_client.httpx.AsyncClient", lambda **kwargs: http)
        rpc = RpcClient(discovery, from_source_secret="test-secret", retries=1)
    try:
        with pytest.raises(RpcError) as caught:
            await rpc.post("wisepen-scholar-service", "/scholar/papers/search", json={"query": "test"})
    finally:
        await rpc.aclose()

    assert caught.value.status == status
    assert caught.value.code == status
    assert caught.value.msg == message
    assert len(requests) == (1 if status == 429 else 2)

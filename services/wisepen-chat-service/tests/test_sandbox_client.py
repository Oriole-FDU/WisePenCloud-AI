from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from common.core.exceptions import RpcError


def _load_sandbox_client_module():
    module_name = "sandbox_client_under_test"
    module_path = (
        Path(__file__).parents[1]
        / "src"
        / "chat"
        / "core"
        / "providers"
        / "sandbox_client.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_sandbox_client = _load_sandbox_client_module()
SandboxClient = _sandbox_client.SandboxClient
SandboxClientError = _sandbox_client.SandboxClientError


class FakeRpc:
    def __init__(self, result=None, exc: Exception | None = None) -> None:
        self.result = result
        self.exc = exc
        self.calls: list[tuple[str, str, str, dict]] = []

    async def request(self, method, service_name, path, *, json=None, timeout=None):
        self.calls.append((method, service_name, path, json or {}))
        if self.exc:
            raise self.exc
        return self.result


@pytest.mark.asyncio
async def test_sandbox_client_uses_rpc_for_allocate_execute_and_release():
    rpc = FakeRpc({
        "lease_id": "lease-1",
        "request_id": "req-1",
        "tenant_id": "user-1",
        "workspace_id": "session-1",
        "fencing_token": 7,
    })
    client = SandboxClient(rpc=rpc, service_name="wisepen-sandbox-service")
    context = {"request_id": "req-1", "user_id": "user-1", "session_id": "session-1"}

    lease = await client.allocate_request(context)
    await client.release_request("req-1")

    assert lease.lease_id == "lease-1"
    assert rpc.calls[0][1] == "wisepen-sandbox-service"
    assert rpc.calls[0][2] == "/internal/sandboxes/allocate"
    assert rpc.calls[1][2] == "/internal/leases/lease-1/release"


@pytest.mark.asyncio
async def test_sandbox_client_maps_rpc_business_error_code():
    rpc = FakeRpc(exc=RpcError(
        service_name="wisepen-sandbox-service",
        path="/internal/sandboxes/allocate",
        code=46001,
        msg="沙箱池暂无可用实例",
    ))
    client = SandboxClient(rpc=rpc, service_name="wisepen-sandbox-service")

    with pytest.raises(SandboxClientError) as exc_info:
        await client.allocate_request({"request_id": "req-empty", "user_id": "u", "session_id": "s"})

    assert exc_info.value.code == "POOL_EMPTY"


@pytest.mark.asyncio
async def test_sandbox_client_maps_workspace_cache_limit_error_code():
    rpc = FakeRpc(exc=RpcError(
        service_name="wisepen-sandbox-service",
        path="/internal/sandboxes/allocate",
        code=46011,
        msg="沙箱工作区缓存超出限制",
    ))
    client = SandboxClient(rpc=rpc, service_name="wisepen-sandbox-service")

    with pytest.raises(SandboxClientError) as exc_info:
        await client.allocate_request({"request_id": "req-cache-limit", "user_id": "u", "session_id": "s"})

    assert exc_info.value.code == "WORKSPACE_CACHE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_sandbox_client_direct_fallback_unwraps_r_response(monkeypatch):
    calls = []

    class Response:
        is_success = True

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            if url.endswith("/internal/sandboxes/allocate"):
                return Response({
                    "code": 200,
                    "msg": "操作成功",
                    "data": {
                        "lease_id": "lease-direct",
                        "request_id": "req-direct",
                        "tenant_id": "user-1",
                        "workspace_id": "session-1",
                        "fencing_token": 11,
                    },
                })
            return Response({
                "code": 200,
                "msg": "操作成功",
                "data": {"data": {"stdout": "ok", "exit_code": 0}},
            })

    monkeypatch.setattr(_sandbox_client.httpx, "AsyncClient", Client)
    client = SandboxClient(base_url="http://127.0.0.1:9001", from_source="secret")

    result = await client.shell_exec(
        {
            "request_id": "req-direct",
            "user_id": "user-1",
            "session_id": "session-1",
        },
        "echo ok",
    )

    assert result["stdout"] == "ok"
    assert calls[0][2]["headers"]["X-From-Source"] == "secret"

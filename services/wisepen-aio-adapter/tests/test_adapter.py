from __future__ import annotations

from types import SimpleNamespace

import pytest

from aio_adapter.models import AdapterConfig
from aio_adapter.path_policy import PathPolicy, PathPolicyError, TenantScope
from aio_adapter.docker_runtime import DockerRuntime
from aio_adapter.client import AioClient
from aio_adapter.errors import AioNotFoundError, AioRequestError
from sandbox.models import SandboxSpec


def test_path_policy_rejects_escape():
    policy = PathPolicy(TenantScope("user_1", "session_1"))
    assert policy.translate("main.py") == "/workspace/main.py"
    with pytest.raises(PathPolicyError):
        policy.translate("/workspace/../../etc/passwd")


def test_docker_runtime_builds_managed_container_commands():
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        output = "container-id\n" if args[1] == "run" else "127.0.0.1:49152\n"
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    runtime = DockerRuntime(AdapterConfig(image="test-image"), runner=runner)
    handle = runtime.create(SandboxSpec("test-image", environment={"MODE": "warm"}))

    assert handle.container_id == "container-id"
    assert handle.endpoint.endswith(":49152")
    assert "wisepen.managed=true" in calls[0]
    assert "test-image" in calls[0]


@pytest.mark.asyncio
async def test_aio_client_sends_token_and_maps_not_found(monkeypatch):
    calls = []

    class Response:
        status_code = 404
        is_success = False

        def json(self):
            return {"detail": "missing"}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    monkeypatch.setattr("aio_adapter.client.httpx.AsyncClient", Client)
    with pytest.raises(AioNotFoundError):
        await AioClient("http://sandbox", token="secret").request("/v1/test", {})
    assert calls[0][1]["headers"]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_aio_client_maps_server_failure_as_retryable(monkeypatch):
    class Response:
        status_code = 503
        is_success = False

        def json(self):
            return {}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            return Response()

    monkeypatch.setattr("aio_adapter.client.httpx.AsyncClient", Client)
    with pytest.raises(AioRequestError) as exc_info:
        await AioClient("http://sandbox").request("/v1/test", {})
    assert exc_info.value.retryable is True

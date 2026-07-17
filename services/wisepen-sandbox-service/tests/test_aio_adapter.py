from __future__ import annotations

from types import SimpleNamespace

import pytest

from sandbox.domain.entities import SandboxSpec
from sandbox.core.providers.aio_adapter.models import AdapterConfig
from sandbox.core.providers.aio_adapter.path_policy import PathPolicy, PathPolicyError, TenantScope
from sandbox.core.providers.aio_adapter.docker_runtime import DockerRuntime
from sandbox.core.providers.aio_adapter.client import AioClient
from sandbox.core.providers.aio_adapter.errors import AioNotFoundError, AioRequestError


def test_path_policy_rejects_escape():
    policy = PathPolicy(TenantScope("user_1", "session_1"))
    assert policy.translate("main.py") == "/workspace/main.py"
    with pytest.raises(PathPolicyError):
        policy.translate("/workspace/../../etc/passwd")


def test_path_policy_can_isolate_a_tenant_workspace():
    policy = PathPolicy(
        TenantScope("tenant_1", "workspace_1"),
        "/home/gem",
        isolate_scope=True,
    )
    assert policy.translate("probe.txt") == "/home/gem/tenant_1/workspace_1/probe.txt"
    assert policy.reverse("/home/gem/tenant_1/workspace_1/probe.txt") == (
        "/home/gem/tenant_1/workspace_1/probe.txt"
    )
    with pytest.raises(PathPolicyError):
        policy.reverse("/home/gem/tenant_2/workspace_1/probe.txt")


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
    assert "-w" not in calls[0]
    assert "-i" in calls[0]
    assert "-t" in calls[0]


def test_docker_runtime_can_mark_e2e_containers():
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        output = "container-id\n" if args[1] == "run" else "127.0.0.1:49152\n"
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    runtime = DockerRuntime(
        AdapterConfig(image="test-image", e2e_label=True), runner=runner
    )
    runtime.create(SandboxSpec("test-image"))
    assert "wisepen.e2e=true" in calls[0]


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

    monkeypatch.setattr("sandbox.core.providers.aio_adapter.client.httpx.AsyncClient", Client)
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

    monkeypatch.setattr("sandbox.core.providers.aio_adapter.client.httpx.AsyncClient", Client)
    with pytest.raises(AioRequestError) as exc_info:
        await AioClient("http://sandbox").request("/v1/test", {})
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_aio_client_uses_real_file_search_and_execute_contract(monkeypatch):
    calls = []

    class Response:
        status_code = 200
        is_success = True

        def json(self):
            return {"success": True, "data": {"ok": True}}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs["json"]))
            return Response()

    monkeypatch.setattr("sandbox.core.providers.aio_adapter.client.httpx.AsyncClient", Client)
    client = AioClient("http://sandbox")
    await client.file_grep("/home/gem/t/w", "alpha", False, True)
    await client.code_execute("python", "print(1)")
    assert calls == [
        (
            "http://sandbox/v1/file/search",
            {"file": "/home/gem/t/w", "regex": "(?i)alpha"},
        ),
        (
            "http://sandbox/v1/code/execute",
            {"language": "python", "code": "print(1)"},
        ),
    ]

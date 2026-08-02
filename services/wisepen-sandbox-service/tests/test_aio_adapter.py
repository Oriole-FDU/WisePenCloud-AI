from __future__ import annotations

from types import SimpleNamespace

import pytest
from common.core.exceptions import ServiceException

from sandbox.domain.entities import Endpoint, Health, SandboxRef, SandboxSpec, WorkspaceSnapshot
from sandbox.domain.error_codes import SandboxErrorCode
from sandbox.core.providers.aio_adapter.models import AdapterConfig
from sandbox.core.providers.aio_adapter.path_policy import PathPolicy, TenantScope
from sandbox.core.providers.aio_adapter.docker_runtime import DockerRuntime
from sandbox.core.providers.aio_adapter.provider import AioSandboxProvider
from sandbox.core.providers.aio_adapter.client import AioClient


def test_path_policy_rejects_escape():
    policy = PathPolicy(TenantScope("user_1", "session_1"))
    assert policy.translate("main.py") == "/workspace/main.py"
    with pytest.raises(ServiceException) as exc_info:
        policy.translate("/workspace/../../etc/passwd")
    assert exc_info.value.code == SandboxErrorCode.WORKSPACE_PATH_INVALID.code


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
    with pytest.raises(ServiceException) as exc_info:
        policy.reverse("/home/gem/tenant_2/workspace_1/probe.txt")
    assert exc_info.value.code == SandboxErrorCode.WORKSPACE_PATH_INVALID.code


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
    assert "wisepen.role=aio-worker" in calls[0]
    assert any(value.startswith("wisepen.owner=wisepen-sandbox-service-") for value in calls[0])
    assert calls[0].count("-p") == 2
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


def test_docker_runtime_passes_browser_no_sandbox_environment():
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        output = "container-id\n" if args[1] == "run" else "127.0.0.1:49152\n"
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    runtime = DockerRuntime(AdapterConfig(image="test-image"), runner=runner)
    runtime.create(
        SandboxSpec(
            "test-image", environment={"BROWSER_NO_SANDBOX": "--no-sandbox"}
        )
    )

    assert "BROWSER_NO_SANDBOX=--no-sandbox" in calls[0]


def test_docker_runtime_maps_command_failure_to_service_error():
    def runner(args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="daemon unavailable")

    runtime = DockerRuntime(AdapterConfig(image="test-image"), runner=runner)
    with pytest.raises(ServiceException) as exc_info:
        runtime.inspect("container-id")
    assert exc_info.value.code == SandboxErrorCode.SANDBOX_UNAVAILABLE.code


def test_docker_runtime_recovers_container_when_docker_cli_is_signaled_after_create():
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        if args[1] == "run":
            return SimpleNamespace(returncode=-5, stdout="", stderr="")
        if args[1:3] == ["container", "inspect"]:
            return SimpleNamespace(returncode=0, stdout="recovered-container\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="127.0.0.1:49152\n", stderr="")

    runtime = DockerRuntime(AdapterConfig(image="test-image"), runner=runner)

    handle = runtime.create(SandboxSpec("test-image"))

    assert handle.container_id == "recovered-container"
    assert sum(args[1] == "run" for args in calls) == 1
    assert any(args[1:3] == ["container", "inspect"] for args in calls)


def test_docker_runtime_retries_signaled_create_only_when_container_is_absent():
    calls = []
    runs = 0

    def runner(args, **kwargs):
        nonlocal runs
        calls.append(args)
        if args[1] == "run":
            runs += 1
            if runs == 1:
                return SimpleNamespace(returncode=-5, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout="container-id\n", stderr="")
        if args[1:3] == ["container", "inspect"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="not found")
        return SimpleNamespace(returncode=0, stdout="127.0.0.1:49152\n", stderr="")

    runtime = DockerRuntime(
        AdapterConfig(image="test-image"), runner=runner, sleeper=lambda _: None
    )

    handle = runtime.create(SandboxSpec("test-image"))

    assert handle.container_id == "container-id"
    assert sum(args[1] == "run" for args in calls) == 2


def test_docker_runtime_redacts_environment_values_in_command_logs():
    redacted = DockerRuntime._redact_args(
        [
            "docker",
            "run",
            "-e",
            "API_TOKEN=secret-value",
            "--env",
            "MODE=warm",
            "--env=PASSWORD=another-secret",
            "test-image",
        ]
    )

    assert redacted == [
        "docker",
        "run",
        "-e",
        "API_TOKEN=<redacted>",
        "--env",
        "MODE=<redacted>",
        "--env=PASSWORD=<redacted>",
        "test-image",
    ]


def test_docker_runtime_preflight_and_public_vnc_urls():
    calls = []

    def runner(args, **kwargs):
        calls.append(args)
        if args[1] == "run":
            output = "container-id\n"
        elif args[1] == "port":
            output = "127.0.0.1:49152\n" if "8080/tcp" in args else "127.0.0.1:49153\n"
        else:
            output = "ok\n"
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    runtime = DockerRuntime(
        AdapterConfig(
            image="test-image",
            network="sandbox-net",
            public_vnc_url_template="https://sandbox.example/vnc/{container_name}",
            public_websocket_url_template="wss://sandbox.example/ws/{port}",
        ),
        runner=runner,
    )
    runtime.validate_deployment()
    handle = runtime.create(SandboxSpec("test-image"))

    assert [call[1:3] for call in calls[:3]] == [
        ["version", "--format"],
        ["image", "inspect"],
        ["network", "inspect"],
    ]
    assert handle.endpoint.startswith("http://wisepen-aio-")
    assert handle.public_vnc_url.startswith("https://sandbox.example/vnc/wisepen-aio-")
    assert handle.public_websocket_url == "wss://sandbox.example/ws/49153"


@pytest.mark.asyncio
async def test_provider_delegates_workspace_transfer():
    class Transfer:
        def __init__(self):
            self.calls = []

        async def copy_in(self, sandbox, snapshot):
            self.calls.append(("in", sandbox, snapshot))

        async def copy_out(self, sandbox, tenant_id, workspace_id):
            self.calls.append(("out", sandbox, tenant_id, workspace_id))
            return WorkspaceSnapshot(tenant_id, workspace_id, {"result.bin": b"\xff"})

        async def checkpoint(
            self, sandbox, tenant_id, workspace_id, lease_id, fencing_token
        ):
            self.calls.append(
                ("checkpoint", sandbox, tenant_id, workspace_id, lease_id, fencing_token)
            )
            return WorkspaceSnapshot(tenant_id, workspace_id, {"saved": "yes"})

    transfer = Transfer()
    provider = AioSandboxProvider(DockerRuntime(AdapterConfig()), transfer)
    sandbox = SandboxRef("sb-1", "container-1", Endpoint("http://worker:8080"))
    snapshot = WorkspaceSnapshot("tenant", "workspace", {"main.py": "print(1)"})

    await provider.prepare_workspace(sandbox, snapshot)
    exported = await provider.export_workspace(sandbox, "tenant", "workspace")
    checkpoint = await provider.checkpoint_workspace(
        sandbox, "tenant", "workspace", "lease-1", 7
    )

    assert transfer.calls[0] == ("in", sandbox, snapshot)
    assert exported.files == {"result.bin": b"\xff"}
    assert checkpoint.files == {"saved": "yes"}


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
    with pytest.raises(ServiceException) as exc_info:
        await AioClient("http://sandbox", token="secret").request("/v1/test", {})
    assert exc_info.value.code == SandboxErrorCode.AIO_RESOURCE_NOT_FOUND.code
    assert calls[0][1]["headers"]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_aio_client_maps_server_failure(monkeypatch):
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
    with pytest.raises(ServiceException) as exc_info:
        await AioClient("http://sandbox").request("/v1/test", {})
    assert exc_info.value.code == SandboxErrorCode.SANDBOX_UNAVAILABLE.code


@pytest.mark.asyncio
async def test_aio_client_health_can_use_a_shorter_warmup_timeout(monkeypatch):
    client_timeouts = []

    class Response:
        status_code = 200
        is_success = True

    class Client:
        def __init__(self, **kwargs):
            client_timeouts.append(kwargs["timeout"])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            return Response()

    monkeypatch.setattr("sandbox.core.providers.aio_adapter.client.httpx.AsyncClient", Client)

    assert await AioClient("http://sandbox", timeout_seconds=30).health(
        timeout_seconds=2
    )
    assert client_timeouts == [2]


@pytest.mark.asyncio
async def test_aio_client_requests_keep_the_business_timeout(monkeypatch):
    client_timeouts = []

    class Response:
        status_code = 200
        is_success = True

        def json(self):
            return {"ok": True}

    class Client:
        def __init__(self, **kwargs):
            client_timeouts.append(kwargs["timeout"])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            return Response()

    monkeypatch.setattr("sandbox.core.providers.aio_adapter.client.httpx.AsyncClient", Client)

    assert await AioClient("http://sandbox", timeout_seconds=30).request("/v1/test", {}) == {
        "ok": True
    }
    assert client_timeouts == [30]


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


@pytest.mark.asyncio
async def test_aio_provider_retries_transient_health_failure_during_warmup():
    provider = AioSandboxProvider(
        DockerRuntime(AdapterConfig(image="test-image")), None
    )
    sandbox = SandboxRef(
        sandbox_id="sb-warmup",
        provider_id="container-warmup",
        endpoint=Endpoint("http://127.0.0.1:49152"),
    )

    class Client:
        def __init__(self):
            self.calls = 0
            self.timeouts = []

        async def health(self, *, timeout_seconds=None):
            self.calls += 1
            self.timeouts.append(timeout_seconds)
            if self.calls == 1:
                raise ServiceException(
                    SandboxErrorCode.SANDBOX_UNAVAILABLE,
                    "transient AIO startup failure",
                )
            return True

    client = Client()
    provider._clients[sandbox.sandbox_id] = client

    result = await provider.wait_ready(sandbox, timeout_seconds=2)

    assert result == Health(True, "ready", attempts=2)
    assert client.calls == 2
    assert all(timeout is not None and 0 < timeout <= 2.0 for timeout in client.timeouts)


@pytest.mark.asyncio
async def test_aio_provider_keeps_warmup_failure_after_persistent_health_failure():
    provider = AioSandboxProvider(
        DockerRuntime(AdapterConfig(image="test-image")), None
    )
    sandbox = SandboxRef(
        sandbox_id="sb-warmup-timeout",
        provider_id="container-warmup-timeout",
        endpoint=Endpoint("http://127.0.0.1:49153"),
    )

    class Client:
        def __init__(self):
            self.calls = 0

        async def health(self, *, timeout_seconds=None):
            self.calls += 1
            raise ServiceException(
                SandboxErrorCode.SANDBOX_UNAVAILABLE,
                "persistent AIO startup failure",
            )

    client = Client()
    provider._clients[sandbox.sandbox_id] = client

    with pytest.raises(TimeoutError, match="未在限定时间内就绪"):
        await provider.wait_ready(sandbox, timeout_seconds=0.01)

    assert client.calls == 1

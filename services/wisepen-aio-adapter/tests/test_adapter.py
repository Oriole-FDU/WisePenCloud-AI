from __future__ import annotations

from types import SimpleNamespace

import pytest

from aio_adapter.models import AdapterConfig
from aio_adapter.path_policy import PathPolicy, PathPolicyError, TenantScope
from aio_adapter.docker_runtime import DockerRuntime
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

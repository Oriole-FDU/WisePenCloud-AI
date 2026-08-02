from __future__ import annotations

from types import SimpleNamespace

import pytest
from common.core.exceptions import ServiceException

from sandbox.application.services.sandbox_session import SandboxSessionService
from sandbox.domain.entities import ExecutionResult
from sandbox.domain.error_codes import SandboxErrorCode
from sandbox.domain.execution_timeout import normalize_execution_timeout_ms


def test_normalize_execution_timeout_uses_default_and_requested_value() -> None:
    assert normalize_execution_timeout_ms(None) == 30000
    assert normalize_execution_timeout_ms(60000) == 60000
    assert normalize_execution_timeout_ms(120000) == 120000


@pytest.mark.parametrize("value", [0, 120001, True, "60000"])
def test_normalize_execution_timeout_rejects_invalid_value(value: object) -> None:
    with pytest.raises(ServiceException) as exc_info:
        normalize_execution_timeout_ms(value)

    assert exc_info.value.code == SandboxErrorCode.INVALID_EXECUTION_TIMEOUT.code
    assert "timeout_ms" in exc_info.value.msg


@pytest.mark.asyncio
async def test_session_normalizes_timeout_before_forwarding(monkeypatch) -> None:
    class Scheduler:
        def __init__(self) -> None:
            self.request = None

        async def execute(self, lease_id, request):
            self.request = request
            return ExecutionResult(request.request_id, "succeeded", {})

    scheduler = Scheduler()
    session = SandboxSessionService(scheduler)
    lease = SimpleNamespace(
        lease_id="lease-1",
        request_id="request-1",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        fencing_token=1,
        sandbox_id="sandbox-1",
    )

    async def allocate():
        return lease

    monkeypatch.setattr(session, "_allocate", allocate)

    await session.execute("execute", {"language": "python", "code": "print(1)"})

    assert scheduler.request.payload["timeout_ms"] == 30000


@pytest.mark.asyncio
async def test_session_rejects_timeout_before_allocating(monkeypatch) -> None:
    session = SandboxSessionService(object())

    async def unexpected_allocate():
        raise AssertionError("invalid timeout must not allocate a sandbox")

    monkeypatch.setattr(session, "_allocate", unexpected_allocate)

    with pytest.raises(ServiceException) as exc_info:
        await session.execute(
            "execute",
            {"language": "python", "code": "print(1)", "timeout_ms": 120001},
        )

    assert exc_info.value.code == SandboxErrorCode.INVALID_EXECUTION_TIMEOUT.code

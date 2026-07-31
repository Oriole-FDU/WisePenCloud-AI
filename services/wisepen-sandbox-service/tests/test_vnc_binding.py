from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from common.core.exceptions import ServiceException

from sandbox.gateway.binding import VncBinding
from sandbox.api import create_app


class FakeSession:
    def __init__(self) -> None:
        self.release_calls: list[tuple[str, str]] = []

    async def acquire_for(self, user_id: str, session_id: str):
        await asyncio.sleep(0)
        return SimpleNamespace(
            sandbox_id="sandbox-1",
            endpoint=SimpleNamespace(
                base_url="http://aio-worker:8080",
                public_vnc_url="https://sandbox.example/vnc/session-1",
                public_websocket_url="wss://sandbox.example/ws/session-1",
            ),
        )

    async def release_for(self, user_id: str, session_id: str) -> None:
        self.release_calls.append((user_id, session_id))


@pytest.mark.asyncio
async def test_vnc_binding_is_idempotent_and_release_is_safe() -> None:
    session = FakeSession()
    binding = VncBinding(session)

    first, second = await asyncio.gather(
        binding.acquire("user-1", "session-1"),
        binding.acquire("user-1", "session-1"),
    )

    assert first == second
    assert first.vnc_url == "https://sandbox.example/vnc/session-1"
    assert first.websocket_url == "wss://sandbox.example/ws/session-1"
    assert (await binding.stats())["active_bindings"] == 1

    await binding.release("user-1", "session-1")
    await binding.release("user-1", "session-1")
    assert session.release_calls == [("user-1", "session-1")]


@pytest.mark.asyncio
async def test_vnc_binding_rejects_missing_public_url() -> None:
    session = FakeSession()

    async def acquire_without_public_url(user_id: str, session_id: str):
        return SimpleNamespace(
            sandbox_id="sandbox-1",
            endpoint=SimpleNamespace(
                base_url="http://aio-worker:8080",
                public_vnc_url=None,
                public_websocket_url=None,
            ),
        )

    session.acquire_for = acquire_without_public_url
    with pytest.raises(ServiceException):
        await VncBinding(session).acquire("user-1", "session-1")


def test_vnc_routes_remain_under_api_gateway_contract() -> None:
    app = create_app(vnc_binding=VncBinding(FakeSession()))
    paths = {route.path for route in app.routes}
    assert {
        "/v1/sandbox/gateway/vnc",
        "/v1/sandbox/gateway/vnc/release",
        "/v1/sandbox/gateway/vnc/status",
    } <= paths
